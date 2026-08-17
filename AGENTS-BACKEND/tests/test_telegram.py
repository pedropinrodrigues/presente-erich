from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_service import FakeAgent
from test_conversation_tools import conversation_settings

from agents_backend.auth import RequestContext
from agents_backend.conversation.service import ConversationService
from agents_backend.conversation.telegram import (
    TelegramClient,
    bind_telegram_account,
    ingest_telegram_update,
    parse_telegram_update,
    telegram_text_chunks,
    verify_telegram_webhook_secret,
)
from agents_backend.models import ChannelAccount, ChannelMessage, Conversation
from agents_backend.schemas import TelegramAccountRequest


def telegram_update(*, text: str, message_id: int = 1) -> dict[str, object]:
    return {
        "update_id": 1000 + message_id,
        "message": {
            "message_id": message_id,
            "date": 1786881600,
            "chat": {"id": 123456789, "type": "private", "first_name": "Pedro"},
            "from": {"id": 123456789, "is_bot": False, "first_name": "Pedro"},
            "text": text,
        },
    }


def test_telegram_secret_parser_and_chunks() -> None:
    settings = conversation_settings(
        TELEGRAM_BOT_TOKEN="bot-token",  # noqa: S106
        TELEGRAM_BOT_USERNAME="test_agent_bot",
        TELEGRAM_WEBHOOK_SECRET="webhook-secret",  # noqa: S106
    )
    assert verify_telegram_webhook_secret("webhook-secret", settings)
    assert not verify_telegram_webhook_secret("wrong", settings)
    parsed = parse_telegram_update(telegram_update(text=" Olá "))
    assert len(parsed) == 1
    assert parsed[0].chat_id == "123456789"
    assert parsed[0].external_message_id == "123456789:1"
    assert parsed[0].text == "Olá"
    assert telegram_text_chunks("a" * 8001) == ["a" * 4000, "a" * 4000, "a"]


@pytest.mark.asyncio
async def test_telegram_binding_and_ingestion_are_idempotent(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = conversation_settings(
        TELEGRAM_BOT_TOKEN="bot-token",  # noqa: S106
        TELEGRAM_BOT_USERNAME="test_agent_bot",
    )
    binding = await bind_telegram_account(
        session,
        context,
        TelegramAccountRequest(display_name="Pedro"),
        settings,
    )
    assert binding.verification_deep_link is not None
    code = parse_qs(urlparse(binding.verification_deep_link).query)["start"][0]
    service = ConversationService(
        settings=settings,
        agent=FakeAgent(),  # type: ignore[arg-type]
    )

    verification = await ingest_telegram_update(
        session,
        telegram_update(text=f"/start {code}", message_id=1),
        service,
    )
    first_payload = telegram_update(text="Quais são minhas pendências?", message_id=2)
    first = await ingest_telegram_update(session, first_payload, service)
    replay = await ingest_telegram_update(session, first_payload, service)

    assert verification == (1, 0)
    assert first == (1, 0)
    assert replay == (1, 1)
    account = await session.scalar(select(ChannelAccount))
    assert account is not None
    assert account.active is True
    assert account.external_account_id == "123456789"
    assert await session.scalar(select(func.count(Conversation.id))) == 1
    assert await session.scalar(select(func.count(ChannelMessage.id))) == 1


@pytest.mark.asyncio
async def test_telegram_client_sends_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/botbot-token/sendMessage")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    settings = conversation_settings(TELEGRAM_BOT_TOKEN="bot-token")  # noqa: S106
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        message_id = await TelegramClient(settings, client).send_text(
            destination="123456789",
            text="Resposta",
        )
    assert message_id == "123456789:42"
