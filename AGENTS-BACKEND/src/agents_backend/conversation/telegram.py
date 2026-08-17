from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings, get_settings
from agents_backend.errors import AppError
from agents_backend.models import ChannelAccount
from agents_backend.schemas import TelegramAccountRequest, TelegramAccountResponse

from .providers import TELEGRAM_PROVIDER
from .service import ConversationService


@dataclass(frozen=True, slots=True)
class TelegramInboundText:
    chat_id: str
    user_id: str
    external_message_id: str
    text: str
    timestamp: str | None


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value)


def verify_telegram_webhook_secret(secret: str | None, settings: Settings) -> bool:
    expected = _secret_value(settings.telegram_webhook_secret)
    return bool(expected and secret and hmac.compare_digest(secret, expected))


def parse_telegram_update(payload: dict[str, Any]) -> list[TelegramInboundText]:
    message = payload.get("message")
    if not isinstance(message, dict):
        return []
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return []
    if chat.get("type") != "private" or sender.get("is_bot") is True:
        return []
    text = str(message.get("text", "")).strip()
    chat_id = str(chat.get("id", ""))
    user_id = str(sender.get("id", ""))
    message_id = str(message.get("message_id", ""))
    if not text or not chat_id or not user_id or not message_id:
        return []
    return [
        TelegramInboundText(
            chat_id=chat_id,
            user_id=user_id,
            external_message_id=f"{chat_id}:{message_id}",
            text=text,
            timestamp=str(message["date"]) if message.get("date") is not None else None,
        )
    ]


def extract_telegram_verification_code(text: str) -> str | None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    command, code = parts
    normalized_command = command.split("@", maxsplit=1)[0].casefold()
    if normalized_command not in {"/start", "/vincular", "vincular"}:
        return None
    if not code or len(code) > 100:
        return None
    return code


async def bind_telegram_account(
    session: AsyncSession,
    context: RequestContext,
    payload: TelegramAccountRequest,
    settings: Settings | None = None,
) -> TelegramAccountResponse:
    selected_settings = settings or get_settings()
    bot_username = selected_settings.telegram_bot_username
    if not bot_username or not _secret_value(selected_settings.telegram_bot_token):
        raise AppError(
            "telegram_not_configured",
            "O bot do Telegram ainda não foi configurado.",
            503,
        )
    account = await session.scalar(
        select(ChannelAccount)
        .where(
            ChannelAccount.provider == TELEGRAM_PROVIDER,
            ChannelAccount.workspace_id == context.workspace_id,
            ChannelAccount.user_id == context.identity.user_id,
        )
        .order_by(ChannelAccount.active.desc(), ChannelAccount.created_at.desc())
        .limit(1)
    )
    if account is not None and account.active:
        return TelegramAccountResponse(
            id=account.id,
            bot_username=bot_username,
            display_name=account.display_name,
            active=True,
            verified_at=account.verified_at,
        )

    code = secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=15)
    code_hash = hashlib.sha256(code.casefold().encode()).hexdigest()
    if account is None:
        account = ChannelAccount(
            workspace_id=context.workspace_id,
            user_id=context.identity.user_id,
            provider=TELEGRAM_PROVIDER,
            external_account_id=f"pending:{uuid.uuid4()}",
            display_name=payload.display_name,
            verification_code_hash=code_hash,
            verification_expires_at=expires_at,
            active=False,
        )
        session.add(account)
    else:
        account.display_name = payload.display_name
        account.verified_at = None
        account.verification_code_hash = code_hash
        account.verification_expires_at = expires_at
        account.active = False
    await session.commit()
    return TelegramAccountResponse(
        id=account.id,
        bot_username=bot_username,
        display_name=account.display_name,
        active=False,
        verified_at=None,
        verification_deep_link=f"https://t.me/{bot_username}?start={code}",
        verification_expires_at=expires_at,
    )


async def verify_telegram_account_from_message(
    session: AsyncSession,
    *,
    chat_id: str,
    text: str,
) -> bool:
    code = extract_telegram_verification_code(text)
    if code is None:
        return False
    now = datetime.now(UTC)
    supplied_hash = hashlib.sha256(code.casefold().encode()).hexdigest()
    account = await session.scalar(
        select(ChannelAccount).where(
            ChannelAccount.provider == TELEGRAM_PROVIDER,
            ChannelAccount.active.is_(False),
            ChannelAccount.verification_expires_at > now,
            ChannelAccount.verification_code_hash == supplied_hash,
        )
    )
    if account is None:
        return False
    account.external_account_id = chat_id
    account.active = True
    account.verified_at = now
    account.verification_code_hash = None
    account.verification_expires_at = None
    await session.commit()
    return True


async def ingest_telegram_update(
    session: AsyncSession,
    payload: dict[str, Any],
    service: ConversationService,
) -> tuple[int, int]:
    accepted = 0
    duplicates = 0
    for message in parse_telegram_update(payload):
        if extract_telegram_verification_code(message.text) is not None:
            if await verify_telegram_account_from_message(
                session,
                chat_id=message.chat_id,
                text=message.text,
            ):
                accepted += 1
            continue
        persisted, replayed = await service.ingest_telegram_text(
            session,
            chat_id=message.chat_id,
            user_id=message.user_id,
            external_message_id=message.external_message_id,
            text=message.text,
            timestamp=message.timestamp,
        )
        if persisted is not None:
            accepted += 1
            duplicates += int(replayed)
    return accepted, duplicates


def telegram_text_chunks(text: str, maximum: int = 4000) -> list[str]:
    remaining = text.strip()
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > maximum:
        split_at = remaining.rfind("\n", 0, maximum + 1)
        if split_at < maximum // 2:
            split_at = remaining.rfind(" ", 0, maximum + 1)
        if split_at < maximum // 2:
            split_at = maximum
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class TelegramClient:
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
        del conversation_metadata
        token = _secret_value(self.settings.telegram_bot_token)
        if not token:
            raise AppError(
                "telegram_not_configured",
                "O bot do Telegram ainda não foi configurado.",
                503,
            )
        chunks = telegram_text_chunks(text)
        if not chunks:
            raise RuntimeError("Mensagem de saída vazia")
        url = f"{self.settings.telegram_api_base_url.rstrip('/')}/bot{token}/sendMessage"
        message_id = ""
        for chunk in chunks:
            if self.client is not None:
                response = await self.client.post(
                    url,
                    json={
                        "chat_id": destination,
                        "text": chunk,
                        "link_preview_options": {"is_disabled": True},
                    },
                )
            else:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.post(
                        url,
                        json={
                            "chat_id": destination,
                            "text": chunk,
                            "link_preview_options": {"is_disabled": True},
                        },
                    )
            response.raise_for_status()
            data = response.json()
            result = data.get("result") or {}
            if not data.get("ok") or result.get("message_id") is None:
                raise RuntimeError("O Telegram não retornou o ID da mensagem")
            message_id = str(result["message_id"])
        return f"{destination}:{message_id}"
