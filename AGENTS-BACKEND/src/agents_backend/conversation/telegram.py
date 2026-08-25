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

from .formatting import telegram_plain_text
from .providers import TELEGRAM_PROVIDER
from .service import ConversationService


@dataclass(frozen=True, slots=True)
class TelegramInboundVoice:
    file_id: str
    file_unique_id: str
    duration_seconds: int
    mime_type: str | None
    file_size_bytes: int | None


@dataclass(frozen=True, slots=True)
class TelegramInboundMessage:
    chat_id: str
    user_id: str
    external_message_id: str
    text: str
    timestamp: str | None
    voice: TelegramInboundVoice | None = None


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value)


def verify_telegram_webhook_secret(secret: str | None, settings: Settings) -> bool:
    expected = _secret_value(settings.telegram_webhook_secret)
    return bool(expected and secret and hmac.compare_digest(secret, expected))


def parse_telegram_update(payload: dict[str, Any]) -> list[TelegramInboundMessage]:
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
    voice_payload = message.get("voice") or message.get("audio")
    voice: TelegramInboundVoice | None = None
    if isinstance(voice_payload, dict):
        file_id = str(voice_payload.get("file_id") or "")
        file_unique_id = str(voice_payload.get("file_unique_id") or "")
        duration = voice_payload.get("duration")
        file_size = voice_payload.get("file_size")
        if file_id and file_unique_id and isinstance(duration, int) and duration >= 0:
            voice = TelegramInboundVoice(
                file_id=file_id,
                file_unique_id=file_unique_id,
                duration_seconds=duration,
                mime_type=(
                    str(voice_payload["mime_type"])
                    if voice_payload.get("mime_type") is not None
                    else None
                ),
                file_size_bytes=file_size if isinstance(file_size, int) else None,
            )
    chat_id = str(chat.get("id", ""))
    user_id = str(sender.get("id", ""))
    message_id = str(message.get("message_id", ""))
    if (not text and voice is None) or not chat_id or not user_id or not message_id:
        return []
    return [
        TelegramInboundMessage(
            chat_id=chat_id,
            user_id=user_id,
            external_message_id=f"{chat_id}:{message_id}",
            text=text,
            timestamp=str(message["date"]) if message.get("date") is not None else None,
            voice=voice,
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
        if message.voice is None and extract_telegram_verification_code(message.text) is not None:
            if await verify_telegram_account_from_message(
                session,
                chat_id=message.chat_id,
                text=message.text,
            ):
                accepted += 1
            continue
        if message.voice is not None:
            persisted, replayed = await service.ingest_telegram_voice(
                session,
                chat_id=message.chat_id,
                user_id=message.user_id,
                external_message_id=message.external_message_id,
                file_id=message.voice.file_id,
                file_unique_id=message.voice.file_unique_id,
                duration_seconds=message.voice.duration_seconds,
                mime_type=message.voice.mime_type,
                file_size_bytes=message.voice.file_size_bytes,
                timestamp=message.timestamp,
            )
        else:
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

    def _token(self) -> str:
        token = _secret_value(self.settings.telegram_bot_token)
        if not token:
            raise AppError(
                "telegram_not_configured",
                "O bot do Telegram ainda não foi configurado.",
                503,
            )
        return token

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self.client is not None:
            response = await self.client.request(method, url, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    async def send_chat_action(self, destination: str, action: str = "typing") -> None:
        token = self._token()
        url = f"{self.settings.telegram_api_base_url.rstrip('/')}/bot{token}/sendChatAction"
        response = await self._request(
            "POST", url, json={"chat_id": destination, "action": action}
        )
        if not response.json().get("ok"):
            raise RuntimeError("O Telegram recusou a indicação de atividade")

    async def download_file(self, file_id: str, maximum_bytes: int) -> bytes:
        token = self._token()
        base_url = self.settings.telegram_api_base_url.rstrip("/")
        metadata_response = await self._request(
            "GET",
            f"{base_url}/bot{token}/getFile",
            params={"file_id": file_id},
        )
        payload = metadata_response.json()
        result = payload.get("result") or {}
        file_path = result.get("file_path")
        file_size = result.get("file_size")
        if not payload.get("ok") or not isinstance(file_path, str) or not file_path:
            raise RuntimeError("O Telegram não retornou o arquivo de áudio")
        if isinstance(file_size, int) and file_size > maximum_bytes:
            raise RuntimeError("O áudio excede o limite permitido")
        audio_response = await self._request(
            "GET", f"{base_url}/file/bot{token}/{file_path}"
        )
        audio = audio_response.content
        if len(audio) > maximum_bytes:
            raise RuntimeError("O áudio excede o limite permitido")
        return audio

    async def send_text(
        self,
        *,
        destination: str,
        text: str,
        conversation_metadata: dict[str, Any] | None = None,
    ) -> str:
        del conversation_metadata
        token = self._token()
        chunks = telegram_text_chunks(telegram_plain_text(text))
        if not chunks:
            raise RuntimeError("Mensagem de saída vazia")
        url = f"{self.settings.telegram_api_base_url.rstrip('/')}/bot{token}/sendMessage"
        message_id = ""
        for chunk in chunks:
            response = await self._request(
                "POST",
                url,
                json={
                    "chat_id": destination,
                    "text": chunk,
                    "link_preview_options": {"is_disabled": True},
                },
            )
            data = response.json()
            result = data.get("result") or {}
            if not data.get("ok") or result.get("message_id") is None:
                raise RuntimeError("O Telegram não retornou o ID da mensagem")
            message_id = str(result["message_id"])
        return f"{destination}:{message_id}"
