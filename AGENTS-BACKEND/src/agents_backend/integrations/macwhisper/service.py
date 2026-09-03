from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import Identity, RequestContext
from agents_backend.config import Settings
from agents_backend.errors import NotFoundError
from agents_backend.ingestion.service import ingest_transcript
from agents_backend.models import (
    AppUser,
    AuditEvent,
    MacWhisperWebhookCredential,
    Source,
)
from agents_backend.schemas import IngestTranscriptResponse, TranscriptEvent

CAPTURE_NAMESPACE = uuid.UUID("0b99cb28-9de8-4d74-a8c0-245314027d50")


class MacWhisperWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=1000)
    transcript: str = Field(min_length=1, max_length=500_000)

    @field_validator("title", "transcript")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("o campo não pode ser vazio")
        return value


@dataclass(frozen=True, slots=True)
class MacWhisperCredentialResult:
    credential_id: uuid.UUID
    status: str
    webhook_url: str | None
    created: bool


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _active_credential(
    session: AsyncSession, context: RequestContext
) -> MacWhisperWebhookCredential | None:
    return await session.scalar(
        select(MacWhisperWebhookCredential)
        .where(
            MacWhisperWebhookCredential.workspace_id == context.workspace_id,
            MacWhisperWebhookCredential.user_id == context.identity.user_id,
            MacWhisperWebhookCredential.status == "active",
        )
        .order_by(MacWhisperWebhookCredential.created_at.desc())
    )


async def active_credential_id(session: AsyncSession, context: RequestContext) -> uuid.UUID | None:
    credential = await _active_credential(session, context)
    return credential.id if credential is not None else None


async def create_webhook_credential(
    session: AsyncSession,
    context: RequestContext,
    settings: Settings,
) -> MacWhisperCredentialResult:
    user = await session.get(AppUser, context.identity.user_id, with_for_update=True)
    if user is None or user.status != "active":
        raise NotFoundError("Conta ativa não encontrada.")
    existing = await _active_credential(session, context)
    if existing is not None:
        return MacWhisperCredentialResult(
            credential_id=existing.id,
            status=existing.status,
            webhook_url=None,
            created=False,
        )
    token = secrets.token_urlsafe(32)
    credential = MacWhisperWebhookCredential(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        token_hash=_token_hash(token),
        status="active",
    )
    session.add(credential)
    await session.flush()
    session.add(
        AuditEvent(
            workspace_id=context.workspace_id,
            actor_user_id=context.identity.user_id,
            operation="macwhisper_webhook_created",
            target_type="macwhisper_webhook",
            target_id=credential.id,
            event_metadata={},
        )
    )
    base_url = str(settings.macwhisper_public_base_url).rstrip("/")
    return MacWhisperCredentialResult(
        credential_id=credential.id,
        status=credential.status,
        webhook_url=f"{base_url}/v1/integrations/macwhisper/webhooks/{token}",
        created=True,
    )


async def revoke_webhook_credential(
    session: AsyncSession,
    context: RequestContext,
) -> bool:
    now = datetime.now(UTC)
    credentials = list(
        (
            await session.scalars(
                select(MacWhisperWebhookCredential)
                .where(
                    MacWhisperWebhookCredential.workspace_id == context.workspace_id,
                    MacWhisperWebhookCredential.user_id == context.identity.user_id,
                    MacWhisperWebhookCredential.status == "active",
                )
                .with_for_update()
            )
        ).all()
    )
    if not credentials:
        return False
    for credential in credentials:
        credential.status = "revoked"
        credential.revoked_at = now
        session.add(
            AuditEvent(
                workspace_id=context.workspace_id,
                actor_user_id=context.identity.user_id,
                operation="macwhisper_webhook_revoked",
                target_type="macwhisper_webhook",
                target_id=credential.id,
                event_metadata={},
            )
        )
    return True


async def revoke_credential_after_delivery_failure(
    session: AsyncSession,
    *,
    credential_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    now = datetime.now(UTC)
    result = await session.execute(
        update(MacWhisperWebhookCredential)
        .where(
            MacWhisperWebhookCredential.id == credential_id,
            MacWhisperWebhookCredential.workspace_id == workspace_id,
            MacWhisperWebhookCredential.user_id == user_id,
            MacWhisperWebhookCredential.status == "active",
        )
        .values(status="revoked", revoked_at=now)
    )
    if int(result.rowcount or 0) > 0:
        session.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                operation="macwhisper_webhook_delivery_failed",
                target_type="macwhisper_webhook",
                target_id=credential_id,
                event_metadata={},
            )
        )


def _capture_id(credential_id: uuid.UUID, payload: MacWhisperWebhookPayload) -> uuid.UUID:
    canonical = json.dumps(
        {"title": payload.title, "transcript": payload.transcript},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return uuid.uuid5(CAPTURE_NAMESPACE, f"{credential_id}:{digest}")


async def ingest_webhook(
    session: AsyncSession,
    token: str,
    payload: MacWhisperWebhookPayload,
    settings: Settings,
) -> IngestTranscriptResponse:
    if not settings.macwhisper_webhook_enabled:
        raise NotFoundError()
    credential = await session.scalar(
        select(MacWhisperWebhookCredential)
        .where(
            MacWhisperWebhookCredential.token_hash == _token_hash(token),
            MacWhisperWebhookCredential.status == "active",
        )
        .with_for_update()
    )
    if credential is None:
        raise NotFoundError()
    user = await session.get(AppUser, credential.user_id)
    if user is None or user.status != "active":
        raise NotFoundError()
    capture_id = _capture_id(credential.id, payload)
    existing = await session.scalar(
        select(Source).where(
            Source.workspace_id == credential.workspace_id,
            Source.capture_id == capture_id,
        )
    )
    captured_at = existing.captured_at if existing is not None else datetime.now(UTC)
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    result, _ = await ingest_transcript(
        session,
        RequestContext(
            identity=Identity(user_id=credential.user_id),
            workspace_id=credential.workspace_id,
        ),
        TranscriptEvent(
            capture_id=capture_id,
            source="macwhisper",
            captured_at=captured_at,
            transcript=payload.transcript,
            language=settings.macwhisper_default_language,
            metadata={
                "title": payload.title.strip(),
                "integration": "macwhisper_custom_webhook_v1",
                "credential_id": str(credential.id),
            },
        ),
        commit=False,
    )
    credential.request_count += 1
    credential.last_used_at = datetime.now(UTC)
    await session.commit()
    return result
