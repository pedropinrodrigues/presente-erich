from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.models import ChannelMessage, Conversation, OutboxMessage

from .service import ConversationService

logger = logging.getLogger(__name__)


class ChannelClient(Protocol):
    async def send_text(
        self,
        *,
        destination: str,
        text: str,
        conversation_metadata: dict[str, Any] | None = None,
    ) -> str: ...


async def claim_channel_message(
    session: AsyncSession,
    worker_id: str,
    providers: tuple[str, ...] | None = None,
) -> ChannelMessage | None:
    now = datetime.now(UTC)
    statement = select(ChannelMessage).where(
        ChannelMessage.direction == "inbound",
        or_(
            (ChannelMessage.status.in_(["received", "retrying"]))
            & (ChannelMessage.available_at <= now),
            (ChannelMessage.status == "processing") & (ChannelMessage.lease_expires_at < now),
        ),
    )
    if providers:
        statement = statement.where(ChannelMessage.provider.in_(providers))
    statement = (
        statement.order_by(ChannelMessage.available_at, ChannelMessage.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    message = await session.scalar(statement)
    if message is None:
        await session.rollback()
        return None
    message.status = "processing"
    message.attempts += 1
    message.locked_by = worker_id
    message.lease_expires_at = now + timedelta(minutes=3)
    message.error_code = None
    await session.commit()
    return message


async def process_channel_message_job(
    session: AsyncSession,
    message: ChannelMessage,
    service: ConversationService,
) -> None:
    message_id = message.id
    try:
        await service.process_channel_message(session, message)
    except Exception as exc:
        await session.rollback()
        fresh = await session.get(ChannelMessage, message_id)
        if fresh is None:
            raise
        retry = fresh.attempts < fresh.max_attempts
        fresh.status = "retrying" if retry else "failed"
        fresh.available_at = datetime.now(UTC) + timedelta(seconds=2**fresh.attempts)
        fresh.locked_by = None
        fresh.lease_expires_at = None
        fresh.error_code = type(exc).__name__
        await session.commit()
        logger.exception("channel_message_processing_failed", extra={"message_id": str(message_id)})


async def claim_outbox_message(
    session: AsyncSession,
    worker_id: str,
    providers: tuple[str, ...] | None = None,
) -> OutboxMessage | None:
    now = datetime.now(UTC)
    statement = select(OutboxMessage).where(
        or_(
            (OutboxMessage.status.in_(["pending", "retrying"]))
            & (OutboxMessage.available_at <= now),
            (OutboxMessage.status == "sending") & (OutboxMessage.lease_expires_at < now),
        )
    )
    if providers:
        statement = statement.where(OutboxMessage.provider.in_(providers))
    statement = (
        statement.order_by(OutboxMessage.available_at, OutboxMessage.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    outbox = await session.scalar(statement)
    if outbox is None:
        await session.rollback()
        return None
    outbox.status = "sending"
    outbox.attempts += 1
    outbox.locked_by = worker_id
    outbox.lease_expires_at = now + timedelta(minutes=2)
    outbox.error_code = None
    await session.commit()
    return outbox


async def process_outbox_message(
    session: AsyncSession,
    outbox: OutboxMessage,
    clients: dict[str, ChannelClient],
) -> None:
    outbox_id = outbox.id
    try:
        conversation = await session.get(Conversation, outbox.conversation_id)
        if conversation is None:
            raise RuntimeError("Conversa da outbox não encontrada")
        client = clients.get(outbox.provider)
        if client is None:
            raise RuntimeError("Provedor de canal não configurado")
        text = str(outbox.payload.get("text", {}).get("body", ""))
        if not text:
            raise RuntimeError("Mensagem de saída vazia")
        provider_message_id = await client.send_text(
            destination=outbox.destination,
            text=text,
            conversation_metadata=conversation.conversation_metadata,
        )
        outbox.status = "sent"
        outbox.provider_message_id = provider_message_id
        outbox.sent_at = datetime.now(UTC)
        outbox.locked_by = None
        outbox.lease_expires_at = None
        if outbox.channel_message_id is not None:
            channel_message = await session.get(ChannelMessage, outbox.channel_message_id)
            if channel_message is not None:
                channel_message.status = "completed"
                channel_message.external_message_id = provider_message_id
        await session.commit()
    except Exception as exc:
        await session.rollback()
        fresh = await session.get(OutboxMessage, outbox_id)
        if fresh is None:
            raise
        retry = fresh.attempts < fresh.max_attempts
        fresh.status = "retrying" if retry else "failed"
        fresh.available_at = datetime.now(UTC) + timedelta(seconds=2**fresh.attempts)
        fresh.locked_by = None
        fresh.lease_expires_at = None
        fresh.error_code = type(exc).__name__
        await session.commit()
        logger.exception("outbox_delivery_failed", extra={"outbox_id": str(outbox_id)})
