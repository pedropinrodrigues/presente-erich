from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings, get_settings
from agents_backend.errors import AppError, ConflictError
from agents_backend.models import ChannelAccount
from agents_backend.schemas import WhatsappAccountRequest, WhatsappAccountResponse

from .phone_numbers import (
    normalize_phone_number,
    whatsapp_phone_aliases,
)
from .providers import WHATSAPP_PROVIDER
from .service import ConversationService


@dataclass(frozen=True, slots=True)
class WhatsAppInboundText:
    sender: str
    phone_number_id: str
    external_message_id: str
    text: str
    timestamp: str | None


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value)


def verify_webhook_signature(body: bytes, signature: str | None, settings: Settings) -> bool:
    app_secret = _secret_value(settings.whatsapp_app_secret)
    if not app_secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def verify_webhook_token(mode: str | None, token: str | None, settings: Settings) -> bool:
    expected = _secret_value(settings.whatsapp_verify_token)
    return bool(expected and mode == "subscribe" and token and hmac.compare_digest(token, expected))


def parse_webhook_messages(payload: dict[str, Any]) -> list[WhatsAppInboundText]:
    result: list[WhatsAppInboundText] = []
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata", {})
            phone_number_id = str(metadata.get("phone_number_id", ""))
            if not phone_number_id:
                continue
            for message in value.get("messages", []):
                if not isinstance(message, dict) or message.get("type") != "text":
                    continue
                text_payload = message.get("text", {})
                text = str(text_payload.get("body", "")).strip()
                try:
                    sender = normalize_phone_number(str(message.get("from", "")))
                except AppError:
                    continue
                external_id = str(message.get("id", ""))
                if text and external_id:
                    result.append(
                        WhatsAppInboundText(
                            sender=sender,
                            phone_number_id=phone_number_id,
                            external_message_id=external_id,
                            text=text,
                            timestamp=(
                                str(message["timestamp"])
                                if message.get("timestamp") is not None
                                else None
                            ),
                        )
                    )
    return result


async def bind_whatsapp_account(
    session: AsyncSession,
    context: RequestContext,
    payload: WhatsappAccountRequest,
) -> WhatsappAccountResponse:
    phone_number = normalize_phone_number(payload.phone_number)
    account = await session.scalar(
        select(ChannelAccount).where(
            ChannelAccount.provider == WHATSAPP_PROVIDER,
            ChannelAccount.external_account_id.in_(whatsapp_phone_aliases(phone_number)),
        )
    )
    now = datetime.now(UTC)
    if account is not None and account.workspace_id != context.workspace_id:
        unavailable = account.active or (
            account.verification_expires_at is not None and account.verification_expires_at > now
        )
        if unavailable:
            raise ConflictError(
                "whatsapp_account_already_linked",
                "Este número já está vinculado ou em verificação por outra conta.",
            )
    if account is not None and account.active:
        return WhatsappAccountResponse(
            id=account.id,
            phone_number=account.external_account_id,
            display_name=account.display_name,
            active=True,
            verified_at=account.verified_at,
        )
    verification_code = secrets.token_hex(8).upper()
    verification_phrase = f"VINCULAR {verification_code}"
    verification_expires_at = now + timedelta(minutes=15)
    verification_hash = hashlib.sha256(verification_phrase.casefold().encode()).hexdigest()
    if account is None:
        account = ChannelAccount(
            workspace_id=context.workspace_id,
            user_id=context.identity.user_id,
            provider=WHATSAPP_PROVIDER,
            external_account_id=phone_number,
            display_name=payload.display_name,
            verified_at=None,
            verification_code_hash=verification_hash,
            verification_expires_at=verification_expires_at,
            active=False,
        )
        session.add(account)
    else:
        account.workspace_id = context.workspace_id
        account.user_id = context.identity.user_id
        account.display_name = payload.display_name
        account.verified_at = None
        account.verification_code_hash = verification_hash
        account.verification_expires_at = verification_expires_at
        account.active = False
    await session.commit()
    return WhatsappAccountResponse(
        id=account.id,
        phone_number=account.external_account_id,
        display_name=account.display_name,
        active=account.active,
        verified_at=account.verified_at,
        verification_phrase=verification_phrase,
        verification_expires_at=verification_expires_at,
    )


async def verify_whatsapp_account_from_message(
    session: AsyncSession, *, sender: str, text: str
) -> bool:
    now = datetime.now(UTC)
    supplied_hash = hashlib.sha256(text.strip().casefold().encode()).hexdigest()
    account = await session.scalar(
        select(ChannelAccount).where(
            ChannelAccount.provider == WHATSAPP_PROVIDER,
            ChannelAccount.external_account_id.in_(whatsapp_phone_aliases(sender)),
            ChannelAccount.active.is_(False),
            ChannelAccount.verification_expires_at > now,
            ChannelAccount.verification_code_hash == supplied_hash,
        )
    )
    if account is None:
        return False
    account.active = True
    account.verified_at = now
    account.verification_code_hash = None
    account.verification_expires_at = None
    await session.commit()
    return True


class WhatsAppClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client

    async def send_text(
        self,
        *,
        destination: str,
        text: str,
        conversation_metadata: dict[str, Any] | None = None,
    ) -> str:
        token = _secret_value(self.settings.whatsapp_access_token)
        version = self.settings.whatsapp_graph_api_version
        selected_phone_id = str((conversation_metadata or {}).get("phone_number_id") or "")
        selected_phone_id = selected_phone_id or self.settings.whatsapp_phone_number_id
        if not token or not version or not selected_phone_id:
            raise AppError(
                "whatsapp_not_configured",
                "As credenciais do WhatsApp ainda não foram configuradas.",
                503,
            )
        url = (
            f"{self.settings.whatsapp_graph_api_base_url.rstrip('/')}/"
            f"{version.strip('/')}/{selected_phone_id}/messages"
        )
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalize_phone_number(destination),
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        if self.client is not None:
            response = await self.client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
        else:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
        response.raise_for_status()
        response_data = response.json()
        messages = response_data.get("messages", [])
        if not messages or not messages[0].get("id"):
            raise RuntimeError("O provedor não retornou o ID da mensagem")
        return str(messages[0]["id"])


async def ingest_webhook_payload(
    session: AsyncSession,
    payload: dict[str, Any],
    service: ConversationService,
) -> tuple[int, int]:
    accepted = 0
    duplicates = 0
    for message in parse_webhook_messages(payload):
        if message.text.casefold().startswith("vincular "):
            if await verify_whatsapp_account_from_message(
                session, sender=message.sender, text=message.text
            ):
                accepted += 1
            continue
        persisted, replayed = await service.ingest_whatsapp_text(
            session,
            sender=message.sender,
            phone_number_id=message.phone_number_id,
            external_message_id=message.external_message_id,
            text=message.text,
            timestamp=message.timestamp,
        )
        if persisted is not None:
            accepted += 1
            duplicates += int(replayed)
    return accepted, duplicates
