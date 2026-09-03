from __future__ import annotations

import hashlib
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_settings

from agents_backend.api.main import create_app
from agents_backend.auth import RequestContext
from agents_backend.conversation.channel_jobs import (
    SENSITIVE_MESSAGE_PLACEHOLDER,
    process_outbox_message,
)
from agents_backend.conversation.telegram_commands import (
    handle_account_command,
    response_contains_sensitive_credential,
)
from agents_backend.db import get_session
from agents_backend.errors import NotFoundError
from agents_backend.integrations.macwhisper.service import (
    MacWhisperWebhookPayload,
    create_webhook_credential,
    ingest_webhook,
    revoke_webhook_credential,
)
from agents_backend.logging import SensitiveRequestPathFilter
from agents_backend.models import (
    ChannelMessage,
    Conversation,
    Job,
    MacWhisperWebhookCredential,
    OutboxMessage,
    Source,
)


def macwhisper_settings():
    return conversation_settings(
        MACWHISPER_WEBHOOK_ENABLED="true",
        MACWHISPER_PUBLIC_BASE_URL="https://api.example.com",
        MACWHISPER_MAX_PAYLOAD_BYTES="600000",
        MACWHISPER_DEFAULT_LANGUAGE="pt-BR",
    )


def _token(webhook_url: str) -> str:
    return urlparse(webhook_url).path.rsplit("/", 1)[1]


@pytest.mark.asyncio
async def test_credential_is_returned_once_and_only_hash_is_stored(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = macwhisper_settings()

    created = await create_webhook_credential(session, context, settings)
    repeated = await create_webhook_credential(session, context, settings)
    assert created.created is True
    assert created.webhook_url is not None
    assert repeated.created is False
    assert repeated.webhook_url is None
    assert repeated.credential_id == created.credential_id

    credential = await session.get(MacWhisperWebhookCredential, created.credential_id)
    token = _token(created.webhook_url)
    assert credential is not None
    assert credential.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in credential.token_hash


@pytest.mark.asyncio
async def test_webhook_ingests_and_replays_transcript_idempotently(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = macwhisper_settings()
    credential = await create_webhook_credential(session, context, settings)
    assert credential.webhook_url is not None
    token = _token(credential.webhook_url)
    payload = MacWhisperWebhookPayload(
        title="Reunião Atlas",
        transcript="Marina confirmou a entrega do Projeto Atlas para sexta-feira.",
    )

    first = await ingest_webhook(session, token, payload, settings)
    replay = await ingest_webhook(session, token, payload, settings)

    assert first.source_id == replay.source_id
    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    source = await session.get(Source, first.source_id)
    stored = await session.get(MacWhisperWebhookCredential, credential.credential_id)
    assert source is not None
    assert source.source_type == "macwhisper"
    assert source.source_metadata["title"] == "Reunião Atlas"
    assert source.language == "pt-BR"
    assert stored is not None and stored.request_count == 2
    assert await session.scalar(select(func.count(Job.id))) == 1

    assert await revoke_webhook_credential(session, context) is True
    with pytest.raises(NotFoundError):
        await ingest_webhook(session, token, payload, settings)


@pytest.mark.asyncio
async def test_telegram_commands_create_and_revoke_personal_url(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = macwhisper_settings()
    response = await handle_account_command(
        session, context, "/macwhisper", settings, provider="telegram"
    )
    assert response is not None
    assert "/v1/integrations/macwhisper/webhooks/" in response
    assert response_contains_sensitive_credential("/macwhisper", response)

    repeated = await handle_account_command(
        session, context, "/macwhisper", settings, provider="telegram"
    )
    assert repeated is not None and "não pode ser exibida novamente" in repeated
    assert not response_contains_sensitive_credential("/macwhisper", repeated)

    revoked = await handle_account_command(
        session, context, "/revogarmacwhisper", settings, provider="telegram"
    )
    assert revoked is not None and "revogada" in revoked
    active = await session.scalar(
        select(func.count(MacWhisperWebhookCredential.id)).where(
            MacWhisperWebhookCredential.status == "active"
        )
    )
    assert active == 0


class CapturingClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[str] = []

    async def send_text(
        self,
        *,
        destination: str,
        text: str,
        conversation_metadata: dict[str, Any] | None = None,
    ) -> str:
        self.messages.append(text)
        if self.fail:
            raise RuntimeError("delivery failed")
        return f"{destination}:sent"


async def _sensitive_outbox(
    session: AsyncSession,
    context: RequestContext,
    text: str,
    *,
    max_attempts: int = 3,
    credential_id: uuid.UUID | None = None,
) -> tuple[ChannelMessage, OutboxMessage]:
    conversation = Conversation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="telegram",
        external_thread_id=str(uuid.uuid4()),
        status="active",
        conversation_metadata={},
    )
    session.add(conversation)
    await session.flush()
    message = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="telegram",
        direction="outbound",
        content=text,
        status="queued",
        message_metadata={
            "sensitive_content": True,
            "sensitive_credential_id": str(credential_id) if credential_id else None,
        },
    )
    session.add(message)
    await session.flush()
    outbox = OutboxMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        channel_message_id=message.id,
        provider="telegram",
        destination="123456",
        payload={"type": "text", "text": {"body": text}},
        status="sending",
        idempotency_key=f"macwhisper:{uuid.uuid4()}",
        attempts=1,
        max_attempts=max_attempts,
    )
    session.add(outbox)
    await session.commit()
    return message, outbox


@pytest.mark.asyncio
async def test_delivered_secret_is_scrubbed_from_conversation_and_outbox(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = macwhisper_settings()
    credential = await create_webhook_credential(session, context, settings)
    assert credential.webhook_url is not None
    message, outbox = await _sensitive_outbox(
        session, context, credential.webhook_url, credential_id=credential.credential_id
    )
    client = CapturingClient()

    await process_outbox_message(session, outbox, {"telegram": client})
    await session.refresh(message)
    await session.refresh(outbox)

    assert client.messages == [credential.webhook_url]
    assert message.content == SENSITIVE_MESSAGE_PLACEHOLDER
    assert outbox.payload["text"]["body"] == SENSITIVE_MESSAGE_PLACEHOLDER
    stored = await session.get(MacWhisperWebhookCredential, credential.credential_id)
    assert stored is not None and stored.status == "active"


@pytest.mark.asyncio
async def test_final_delivery_failure_revokes_and_scrubs_credential(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = macwhisper_settings()
    credential = await create_webhook_credential(session, context, settings)
    assert credential.webhook_url is not None
    message, outbox = await _sensitive_outbox(
        session,
        context,
        credential.webhook_url,
        max_attempts=1,
        credential_id=credential.credential_id,
    )

    await process_outbox_message(session, outbox, {"telegram": CapturingClient(fail=True)})
    await session.refresh(message)
    await session.refresh(outbox)
    stored = await session.get(MacWhisperWebhookCredential, credential.credential_id)

    assert outbox.status == "failed"
    assert message.content == SENSITIVE_MESSAGE_PLACEHOLDER
    assert stored is not None and stored.status == "revoked"


def test_access_log_filter_redacts_webhook_token() -> None:
    import logging

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1",
            "POST",
            "/v1/integrations/macwhisper/webhooks/secret-value-12345678901234567890",
            "1.1",
            202,
        ),
        None,
    )

    assert SensitiveRequestPathFilter().filter(record)
    assert "secret-value" not in record.getMessage()
    assert "/webhooks/[redacted]" in record.getMessage()


@pytest.mark.asyncio
async def test_http_webhook_accepts_official_payload_and_replay(
    session: AsyncSession,
    context: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = macwhisper_settings()
    credential = await create_webhook_credential(session, context, settings)
    assert credential.webhook_url is not None

    async def session_override():
        yield session

    monkeypatch.setattr("agents_backend.api.macwhisper_routes.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_session] = session_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.com") as client:
        first = await client.post(
            urlparse(credential.webhook_url).path,
            json={"title": "Teste MacWhisper", "transcript": "Transcrição de integração."},
        )
        replay = await client.post(
            urlparse(credential.webhook_url).path,
            json={"title": "Teste MacWhisper", "transcript": "Transcrição de integração."},
        )
        invalid = await client.post(
            "/v1/integrations/macwhisper/webhooks/invalid",
            json={"title": "Teste", "transcript": "Não deve entrar."},
        )

    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert first.headers["cache-control"] == "no-store"
    assert invalid.status_code == 404
