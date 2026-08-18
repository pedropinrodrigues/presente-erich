from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.conversation.providers import API_PROVIDER
from agents_backend.models import (
    ChannelAccount,
    ChannelMessage,
    Conversation,
    OrchestrationTask,
    OrchestrationTaskEvent,
    OrchestrationTaskStatus,
    OutboxMessage,
)

from .runtime import OrchestrationAgent, OrchestrationResult

logger = logging.getLogger(__name__)


async def claim_orchestration_task(
    session: AsyncSession,
    worker_id: str,
) -> OrchestrationTask | None:
    now = datetime.now(UTC)
    task = await session.scalar(
        select(OrchestrationTask)
        .where(
            or_(
                (
                    OrchestrationTask.status.in_(
                        [
                            OrchestrationTaskStatus.QUEUED.value,
                            OrchestrationTaskStatus.RUNNING.value,
                        ]
                    )
                )
                & (OrchestrationTask.available_at <= now)
                & (
                    (OrchestrationTask.status == OrchestrationTaskStatus.QUEUED.value)
                    | (OrchestrationTask.lease_expires_at < now)
                ),
            )
        )
        .order_by(OrchestrationTask.available_at, OrchestrationTask.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if task is None:
        await session.rollback()
        return None
    task.status = OrchestrationTaskStatus.RUNNING.value
    task.attempts += 1
    task.started_at = task.started_at or now
    task.locked_by = worker_id
    task.lease_expires_at = now + timedelta(minutes=5)
    task.error_code = None
    session.add(
        OrchestrationTaskEvent(
            workspace_id=task.workspace_id,
            orchestration_task_id=task.id,
            event_type="started",
            event_metadata={"attempt": task.attempts},
        )
    )
    await session.commit()
    return task


class OrchestrationService:
    def __init__(self, agent: OrchestrationAgent) -> None:
        self.agent = agent

    async def _destination(
        self,
        session: AsyncSession,
        conversation: Conversation,
        inbound: ChannelMessage,
    ) -> str:
        destination = str(inbound.message_metadata.get("sender") or "")
        if conversation.channel_account_id is not None:
            account = await session.get(ChannelAccount, conversation.channel_account_id)
            if account is not None and account.provider == conversation.provider:
                destination = account.external_account_id
        return destination

    async def _persist_result(
        self,
        session: AsyncSession,
        task: OrchestrationTask,
        answer: str,
    ) -> None:
        existing = await session.scalar(
            select(ChannelMessage).where(
                ChannelMessage.conversation_id == task.conversation_id,
                ChannelMessage.direction == "outbound",
                ChannelMessage.message_metadata["orchestration_task_id"].as_string()
                == str(task.id),
            )
        )
        if existing is not None:
            return

        conversation = await session.get(Conversation, task.conversation_id)
        inbound = await session.get(ChannelMessage, task.inbound_message_id)
        if conversation is None or inbound is None:
            raise RuntimeError("Conversa da tarefa não encontrada")
        outbound = ChannelMessage(
            workspace_id=task.workspace_id,
            conversation_id=task.conversation_id,
            reply_to_message_id=task.inbound_message_id,
            provider=conversation.provider,
            direction="outbound",
            content=answer,
            status="completed" if conversation.provider == API_PROVIDER else "queued",
            message_metadata={
                "response_phase": "orchestration_result",
                "orchestration_task_id": str(task.id),
            },
        )
        session.add(outbound)
        await session.flush()
        if conversation.provider == API_PROVIDER:
            return

        outbox = OutboxMessage(
            workspace_id=task.workspace_id,
            conversation_id=task.conversation_id,
            channel_message_id=outbound.id,
            depends_on_outbox_id=task.ack_outbox_id,
            provider=conversation.provider,
            destination=await self._destination(session, conversation, inbound),
            payload={"type": "text", "text": {"body": answer}},
            status="pending",
            idempotency_key=f"orchestration-result:{task.id}",
        )
        session.add(outbox)
        await session.flush()
        task.result_outbox_id = outbox.id

    async def process(
        self,
        session: AsyncSession,
        task: OrchestrationTask,
    ) -> OrchestrationResult:
        result = await self.agent.run(session, task)
        fresh = await session.get(OrchestrationTask, task.id)
        if fresh is None:
            raise RuntimeError("Tarefa de orquestração desapareceu")
        if result.pending_action is not None:
            fresh.status = OrchestrationTaskStatus.WAITING_CONFIRMATION.value
            fresh.result_code = "confirmation_required"
        else:
            fresh.status = OrchestrationTaskStatus.COMPLETED.value
            fresh.result_code = "completed"
            fresh.completed_at = datetime.now(UTC)
        fresh.locked_by = None
        fresh.lease_expires_at = None
        await self._persist_result(session, fresh, result.answer)
        session.add(
            OrchestrationTaskEvent(
                workspace_id=fresh.workspace_id,
                orchestration_task_id=fresh.id,
                event_type=(
                    "waiting_confirmation"
                    if result.pending_action is not None
                    else "completed"
                ),
                event_metadata={"run_id": str(result.run_id)},
            )
        )
        await session.commit()
        return result

    async def fail(
        self,
        session: AsyncSession,
        task_id: object,
        error: Exception,
    ) -> None:
        task = await session.get(OrchestrationTask, task_id)
        if task is None:
            raise error
        retry = task.attempts < task.max_attempts
        task.status = (
            OrchestrationTaskStatus.QUEUED.value
            if retry
            else OrchestrationTaskStatus.FAILED.value
        )
        task.available_at = datetime.now(UTC) + timedelta(seconds=2**task.attempts)
        task.locked_by = None
        task.lease_expires_at = None
        task.error_code = type(error).__name__
        session.add(
            OrchestrationTaskEvent(
                workspace_id=task.workspace_id,
                orchestration_task_id=task.id,
                event_type="retrying" if retry else "failed",
                event_metadata={"attempt": task.attempts, "error_code": type(error).__name__},
            )
        )
        if not retry:
            task.completed_at = datetime.now(UTC)
            task.result_code = "failed"
            await self._persist_result(
                session,
                task,
                "Não consegui concluir esta tarefa agora. Tente novamente em uma nova mensagem.",
            )
        await session.commit()


async def process_orchestration_task_job(
    session: AsyncSession,
    task: OrchestrationTask,
    service: OrchestrationService,
) -> None:
    task_id = task.id
    try:
        await service.process(session, task)
    except Exception as exc:
        await session.rollback()
        await service.fail(session, task_id, exc)
        logger.exception("orchestration_task_failed", extra={"task_id": str(task_id)})

