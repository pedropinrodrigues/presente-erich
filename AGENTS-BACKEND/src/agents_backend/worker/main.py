from __future__ import annotations

import asyncio
import logging

from agents_backend.config import get_settings
from agents_backend.conversation.channel_jobs import (
    ChannelClient,
    claim_channel_message,
    claim_outbox_message,
    process_channel_message_job,
    process_outbox_message,
)
from agents_backend.conversation.providers import TELEGRAM_PROVIDER, WHATSAPP_PROVIDER
from agents_backend.conversation.runtime import ConversationAgent
from agents_backend.conversation.service import ConversationService
from agents_backend.conversation.telegram import TelegramClient
from agents_backend.conversation.whatsapp import WhatsAppClient
from agents_backend.db import get_engine, get_session_factory
from agents_backend.logging import configure_logging
from agents_backend.model_gateway.client import ModelGateway
from agents_backend.worker.service import claim_job, default_worker_id, process_job

logger = logging.getLogger(__name__)


async def worker_loop() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker_id = default_worker_id()
    gateway = ModelGateway(settings)
    conversation_service = ConversationService(
        settings=settings,
        agent=ConversationAgent(settings=settings, gateway=gateway),
    )
    clients: dict[str, ChannelClient]
    if settings.messaging_provider == TELEGRAM_PROVIDER:
        clients = {TELEGRAM_PROVIDER: TelegramClient(settings)}
    else:
        clients = {WHATSAPP_PROVIDER: WhatsAppClient(settings)}
    active_providers = (settings.messaging_provider,)
    logger.info("worker_started")
    while True:
        try:
            processed = False
            async with get_session_factory()() as session:
                channel_message = await claim_channel_message(
                    session, worker_id, active_providers
                )
                if channel_message is not None:
                    await process_channel_message_job(
                        session, channel_message, conversation_service
                    )
                    processed = True
            async with get_session_factory()() as session:
                outbox_message = await claim_outbox_message(
                    session, worker_id, active_providers
                )
                if outbox_message is not None:
                    await process_outbox_message(session, outbox_message, clients)
                    processed = True
            async with get_session_factory()() as session:
                job = await claim_job(session, worker_id)
                if job is not None:
                    await process_job(session, job, gateway)
                    processed = True
            if not processed:
                await asyncio.sleep(settings.worker_poll_interval_seconds)
        except Exception:
            logger.exception("worker_cycle_failed")
            await get_engine().dispose()
            await asyncio.sleep(settings.worker_poll_interval_seconds)


def run() -> None:
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("worker_stopped")


if __name__ == "__main__":
    run()
