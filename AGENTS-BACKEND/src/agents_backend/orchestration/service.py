from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.conversation.formatting import format_channel_text
from agents_backend.conversation.providers import API_PROVIDER
from agents_backend.models import (
    ChannelAccount,
    ChannelMessage,
    Conversation,
    OrchestrationTask,
    OrchestrationTaskEvent,
    OrchestrationTaskStatus,
    OutboxMessage,
    PendingAction,
)

from .runtime import OrchestrationAgent, OrchestrationResult

logger = logging.getLogger(__name__)


async def reconcile_closed_pending_task(session: AsyncSession) -> bool:
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(OrchestrationTask, PendingAction)
            .join(PendingAction, PendingAction.orchestration_task_id == OrchestrationTask.id)
            .where(
                OrchestrationTask.status == OrchestrationTaskStatus.WAITING_CONFIRMATION.value,
                or_(
                    PendingAction.status.in_(["expired", "failed", "cancelled", "executed"]),
                    (PendingAction.status == "pending") & (PendingAction.expires_at <= now),
                ),
            )
            .order_by(PendingAction.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).first()
    if row is None:
        await session.rollback()
        return False
    task, action = row
    if action.status == "pending":
        action.status = "expired"
    if action.status == "executed":
        task.status = OrchestrationTaskStatus.COMPLETED.value
        task.result_code = "confirmed_action_executed"
        event_type = "reconciled_completed"
    elif action.status == "cancelled":
        task.status = OrchestrationTaskStatus.CANCELLED.value
        task.result_code = "pending_action_cancelled"
        event_type = "reconciled_cancelled"
    else:
        task.status = OrchestrationTaskStatus.FAILED.value
        task.result_code = "pending_action_closed"
        task.error_code = f"pending_action_{action.status}"
        event_type = "reconciled_failed"
    task.completed_at = now
    task.locked_by = None
    task.lease_expires_at = None
    session.add(
        OrchestrationTaskEvent(
            workspace_id=task.workspace_id,
            orchestration_task_id=task.id,
            event_type=event_type,
            event_metadata={"pending_action_id": str(action.id), "action_status": action.status},
        )
    )
    await session.commit()
    return True


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
        secure_result = task.routing_context.get("secure_result_text")
        selected_answer = str(secure_result) if secure_result else answer
        outbound_text = format_channel_text(conversation.provider, selected_answer)
        outbound = ChannelMessage(
            workspace_id=task.workspace_id,
            conversation_id=task.conversation_id,
            reply_to_message_id=task.inbound_message_id,
            provider=conversation.provider,
            direction="outbound",
            content=outbound_text,
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
            payload={"type": "text", "text": {"body": outbound_text}},
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
        from agents_backend.scheduling.dispatcher import finish_scheduled_run_for_task

        scheduled_success = not any(item.status == "failed" for item in result.tools_used)
        await finish_scheduled_run_for_task(
            session,
            fresh,
            success=scheduled_success,
            error_code=None if scheduled_success else "scheduled_tool_failed",
        )
        session.add(
            OrchestrationTaskEvent(
                workspace_id=fresh.workspace_id,
                orchestration_task_id=fresh.id,
                event_type=(
                    "waiting_confirmation" if result.pending_action is not None else "completed"
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
            OrchestrationTaskStatus.QUEUED.value if retry else OrchestrationTaskStatus.FAILED.value
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
            is_scheduled = task.routing_context.get("route") == "scheduled"
            failure_message = (
                "Não consegui executar esta rotina agora. Ela foi marcada como precisando de "
                "atenção; você pode revisar ou executar novamente."
                if is_scheduled
                else (
                    "Não consegui concluir esta tarefa agora. Tente novamente em uma nova mensagem."
                )
            )
            await self._persist_result(session, task, failure_message)
            from agents_backend.scheduling.dispatcher import finish_scheduled_run_for_task

            await finish_scheduled_run_for_task(
                session,
                task,
                success=False,
                error_code=type(error).__name__,
            )
            if is_scheduled:
                from agents_backend.models import ScheduledAutomation, ScheduledAutomationStatus

                raw_schedule_id = task.routing_context.get("scheduled_automation_id")
                try:
                    schedule_id = uuid.UUID(str(raw_schedule_id))
                except ValueError:
                    schedule_id = None
                schedule = (
                    await session.get(ScheduledAutomation, schedule_id)
                    if schedule_id is not None
                    else None
                )
                if schedule is not None:
                    schedule.status = ScheduledAutomationStatus.NEEDS_ATTENTION.value
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
