from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_service import FakeAgent
from test_conversation_tools import conversation_settings

from agents_backend.auth import RequestContext
from agents_backend.conversation.phone_numbers import (
    phone_numbers_equivalent,
    whatsapp_phone_aliases,
)
from agents_backend.conversation.service import ConversationService
from agents_backend.conversation.whatsapp import (
    bind_whatsapp_account,
    ingest_webhook_payload,
    parse_webhook_messages,
    verify_webhook_signature,
    verify_webhook_token,
)
from agents_backend.models import ChannelAccount, ChannelMessage, Conversation
from agents_backend.schemas import WhatsappAccountRequest


def webhook_payload(
    *,
    text: str = "Quais são minhas pendências?",
    message_id: str = "wamid.message-1",
    sender: str = "5511999999999",
) -> dict[str, object]:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "business-phone-id"},
                            "messages": [
                                {
                                    "from": sender,
                                    "id": message_id,
                                    "timestamp": "1786881600",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


def test_webhook_signature_token_and_parser() -> None:
    settings = conversation_settings(
        WHATSAPP_APP_SECRET="app-secret",  # noqa: S106
        WHATSAPP_VERIFY_TOKEN="verify-token",  # noqa: S106
    )
    body = json.dumps(webhook_payload()).encode()
    signature = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(body, signature, settings) is True
    assert verify_webhook_signature(body + b"x", signature, settings) is False
    assert verify_webhook_token("subscribe", "verify-token", settings) is True
    parsed = parse_webhook_messages(webhook_payload())
    assert len(parsed) == 1
    assert parsed[0].external_message_id == "wamid.message-1"
    assert parsed[0].sender == "5511999999999"


def test_brazilian_whatsapp_number_aliases() -> None:
    with_ninth_digit = "5584998765432"
    provider_wa_id = "558498765432"

    assert provider_wa_id in whatsapp_phone_aliases(with_ninth_digit)
    assert with_ninth_digit in whatsapp_phone_aliases(provider_wa_id)
    assert phone_numbers_equivalent(with_ninth_digit, provider_wa_id)
    assert not phone_numbers_equivalent(with_ninth_digit, "558481154324")


@pytest.mark.asyncio
async def test_whatsapp_webhook_is_linked_and_idempotent(
    session: AsyncSession, context: RequestContext
) -> None:
    settings = conversation_settings()
    binding = await bind_whatsapp_account(
        session,
        context,
        WhatsappAccountRequest(phone_number="+55 (11) 99999-9999", display_name="Pedro"),
    )
    service = ConversationService(
        settings=settings,
        agent=FakeAgent(),  # type: ignore[arg-type]
    )

    assert binding.active is False
    assert binding.verification_phrase is not None
    verification = await ingest_webhook_payload(
        session,
        webhook_payload(
            text=binding.verification_phrase,
            message_id="wamid.link",
            sender="551199999999",
        ),
        service,
    )
    first_payload = webhook_payload(sender="551199999999")
    first = await ingest_webhook_payload(session, first_payload, service)
    replay = await ingest_webhook_payload(session, first_payload, service)

    assert verification == (1, 0)
    assert first == (1, 0)
    assert replay == (1, 1)
    assert await session.scalar(select(func.count(Conversation.id))) == 1
    assert await session.scalar(select(func.count(ChannelMessage.id))) == 1
    account = await session.scalar(select(ChannelAccount))
    assert account is not None
    assert account.external_account_id == "5511999999999"
