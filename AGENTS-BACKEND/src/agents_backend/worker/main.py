from __future__ import annotations

import asyncio
import logging
import re
import time

from agents_backend.config import Settings, get_settings
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
from agents_backend.orchestration.runtime import OrchestrationAgent
from agents_backend.orchestration.service import (
    OrchestrationService,
    claim_orchestration_task,
    process_orchestration_task_job,
    reconcile_closed_pending_task,
)
from agents_backend.scheduling.dispatcher import (
    claim_scheduled_run,
    dispatch_due_schedule,
    expire_stale_schedules,
    process_scheduled_run_job,
)
from agents_backend.worker.health import record_worker_heartbeat
from agents_backend.worker.service import claim_job, default_worker_id, process_job

logger = logging.getLogger(__name__)


def _safe_error_message(error: Exception) -> str:
    message = " ".join(str(error).split())[:300]
    message = re.sub(r"(?i)(password|token|api[_-]?key)=([^\s,;]+)", r"\1=[redacted]", message)
    message = re.sub(r"(://[^:/\s]+:)[^@\s]+@", r"\1[redacted]@", message)
    return message or type(error).__name__


async def _worker_cycle(
    *,
    settings: Settings,
    worker_id: str,
    conversation_service: ConversationService,
    orchestration_service: OrchestrationService,
    clients: dict[str, ChannelClient],
    active_providers: tuple[str, ...],
    gateway: ModelGateway,
    stage: list[str],
) -> bool:
    processed = False
    stage[0] = "channel_message"
    async with get_session_factory()() as session:
        channel_message = await claim_channel_message(session, worker_id, active_providers)
        if channel_message is not None:
            await process_channel_message_job(session, channel_message, conversation_service)
            processed = True
    stage[0] = "outbox"
    async with get_session_factory()() as session:
        outbox_message = await claim_outbox_message(session, worker_id, active_providers)
        if outbox_message is not None:
            await process_outbox_message(session, outbox_message, clients)
            processed = True
    stage[0] = "orchestration"
    async with get_session_factory()() as session:
        orchestration_task = await claim_orchestration_task(session, worker_id)
        if orchestration_task is not None:
            await process_orchestration_task_job(
                session, orchestration_task, orchestration_service
            )
            processed = True
    stage[0] = "pending_action_reconciliation"
    async with get_session_factory()() as session:
        if await reconcile_closed_pending_task(session):
            processed = True
    if settings.scheduler_enabled:
        stage[0] = "schedule_expiration"
        async with get_session_factory()() as session:
            if await expire_stale_schedules(session):
                processed = True
        stage[0] = "schedule_dispatch"
        async with get_session_factory()() as session:
            if await dispatch_due_schedule(session, settings):
                processed = True
        stage[0] = "scheduled_run"
        async with get_session_factory()() as session:
            scheduled_run = await claim_scheduled_run(session, worker_id, settings)
            if scheduled_run is not None:
                await process_scheduled_run_job(session, scheduled_run, settings)
                processed = True
    stage[0] = "ingestion"
    async with get_session_factory()() as session:
        job = await claim_job(session, worker_id)
        if job is not None:
            await process_job(session, job, gateway)
            processed = True
    stage[0] = "idle"
    return processed


async def worker_loop() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker_id = default_worker_id()
    gateway = ModelGateway(settings)
    conversation_service = ConversationService(
        settings=settings,
        agent=ConversationAgent(settings=settings, gateway=gateway),
    )
    orchestration_service = OrchestrationService(
        OrchestrationAgent(settings=settings, gateway=gateway)
    )
    clients: dict[str, ChannelClient]
    if settings.messaging_provider == TELEGRAM_PROVIDER:
        clients = {TELEGRAM_PROVIDER: TelegramClient(settings)}
    else:
        clients = {WHATSAPP_PROVIDER: WhatsAppClient(settings)}
    active_providers = (settings.messaging_provider,)
    consecutive_failures = 0
    last_heartbeat = 0.0
    stage = ["startup"]
    logger.info(
        "worker_started",
        extra={
            "worker_id": worker_id,
            "deployment_revision": settings.deployment_revision or "local",
        },
    )
    while True:
        try:
            async with asyncio.timeout(settings.worker_cycle_timeout_seconds):
                processed = await _worker_cycle(
                    settings=settings,
                    worker_id=worker_id,
                    conversation_service=conversation_service,
                    orchestration_service=orchestration_service,
                    clients=clients,
                    active_providers=active_providers,
                    gateway=gateway,
                    stage=stage,
                )
            consecutive_failures = 0
            if time.monotonic() - last_heartbeat >= settings.worker_heartbeat_interval_seconds:
                stage[0] = "heartbeat"
                async with get_session_factory()() as session:
                    snapshot = await record_worker_heartbeat(
                        session,
                        worker_id=worker_id,
                        status="healthy",
                        consecutive_infra_failures=0,
                        settings=settings,
                    )
                queue_lag = int(snapshot["max_lag_seconds"])
                if queue_lag >= settings.queue_lag_warning_seconds:
                    logger.warning(
                        "worker_queue_lag_high",
                        extra={"worker_id": worker_id, "queue_lag_seconds": queue_lag},
                    )
                last_heartbeat = time.monotonic()
            if not processed:
                await asyncio.sleep(settings.worker_poll_interval_seconds)
        except Exception as exc:
            consecutive_failures += 1
            logger.exception(
                "worker_cycle_failed",
                extra={
                    "worker_stage": stage[0],
                    "worker_id": worker_id,
                    "error_type": type(exc).__name__,
                    "error_message": _safe_error_message(exc),
                    "consecutive_failures": consecutive_failures,
                },
            )
            await get_engine().dispose()
            if consecutive_failures >= settings.worker_max_consecutive_infra_failures:
                logger.critical(
                    "worker_unhealthy_exiting",
                    extra={
                        "worker_stage": stage[0],
                        "worker_id": worker_id,
                        "consecutive_failures": consecutive_failures,
                    },
                )
                raise RuntimeError("worker infrastructure failure threshold reached") from exc
            backoff = min(30.0, max(settings.worker_poll_interval_seconds, 2**consecutive_failures))
            await asyncio.sleep(backoff)


def run() -> None:
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("worker_stopped")


if __name__ == "__main__":
    run()
