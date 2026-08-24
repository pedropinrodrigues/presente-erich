from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import Identity, RequestContext
from agents_backend.config import Settings
from agents_backend.conversation.formatting import format_channel_text
from agents_backend.conversation.providers import API_PROVIDER
from agents_backend.models import (
    AutomationGrant,
    ChannelAccount,
    ChannelMessage,
    Conversation,
    OrchestrationIntent,
    OrchestrationTask,
    OrchestrationTaskEvent,
    OrchestrationTaskStatus,
    OutboxMessage,
    PendingAction,
    ScheduledAutomation,
    ScheduledAutomationStatus,
    ScheduledRun,
    ScheduledRunStatus,
    ScheduleEvent,
)
from agents_backend.profile.service import get_user_context_profile
from agents_backend.scheduling.recurrence import next_occurrence
from agents_backend.scheduling.schemas import ScheduleSpec

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def expire_stale_schedules(session: AsyncSession) -> bool:
    now = datetime.now(UTC)
    candidates = list(
        (
            await session.scalars(
                select(ScheduledAutomation)
                .where(
                    ScheduledAutomation.status
                    == ScheduledAutomationStatus.AWAITING_CONFIRMATION.value,
                    ScheduledAutomation.next_run_at.is_not(None),
                    ScheduledAutomation.next_run_at < now,
                )
                .order_by(ScheduledAutomation.next_run_at)
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        ).all()
    )
    schedule = next(
        (
            item
            for item in candidates
            if item.next_run_at is not None
            and _as_utc(item.next_run_at) + timedelta(seconds=item.misfire_grace_seconds) < now
        ),
        None,
    )
    if schedule is None:
        await session.rollback()
        return False
    schedule.status = ScheduledAutomationStatus.EXPIRED.value
    schedule.next_run_at = None
    grants = list(
        (
            await session.scalars(
                select(AutomationGrant).where(
                    AutomationGrant.scheduled_automation_id == schedule.id,
                    AutomationGrant.status == "pending",
                )
            )
        ).all()
    )
    for grant in grants:
        grant.status = "revoked"
        grant.revoked_at = now
    actions = list(
        (
            await session.scalars(
                select(PendingAction).where(
                    PendingAction.workspace_id == schedule.workspace_id,
                    PendingAction.conversation_id == schedule.conversation_id,
                    PendingAction.user_id == schedule.user_id,
                    PendingAction.tool_name == "activate_schedule",
                    PendingAction.status == "pending",
                )
            )
        ).all()
    )
    for action in actions:
        if str(action.arguments.get("schedule_id")) == str(schedule.id):
            action.status = "expired"
    session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="expired",
            event_metadata={"revision": schedule.revision, "reason": "confirmation_timeout"},
        )
    )
    await session.commit()
    return True


async def dispatch_due_schedule(
    session: AsyncSession,
    settings: Settings,
) -> bool:
    now = datetime.now(UTC)
    schedule = await session.scalar(
        select(ScheduledAutomation)
        .where(
            ScheduledAutomation.status == ScheduledAutomationStatus.ACTIVE.value,
            ScheduledAutomation.next_run_at.is_not(None),
            ScheduledAutomation.next_run_at <= now,
        )
        .order_by(ScheduledAutomation.next_run_at, ScheduledAutomation.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if schedule is None or schedule.next_run_at is None:
        await session.rollback()
        return False
    scheduled_for = _as_utc(schedule.next_run_at)
    spec = ScheduleSpec.model_validate(schedule.compiled_spec)
    late_seconds = max(0, int((now - scheduled_for).total_seconds()))
    skipped = late_seconds > schedule.misfire_grace_seconds or (
        schedule.misfire_policy == "skip" and late_seconds > 0
    )
    run = ScheduledRun(
        workspace_id=schedule.workspace_id,
        user_id=schedule.user_id,
        scheduled_automation_id=schedule.id,
        automation_revision=schedule.revision,
        scheduled_for=scheduled_for,
        status=(ScheduledRunStatus.SKIPPED.value if skipped else ScheduledRunStatus.QUEUED.value),
        manual=False,
        max_attempts=settings.schedule_max_run_attempts,
        completed_at=now if skipped else None,
        result_code="misfire_skipped" if skipped else None,
    )
    session.add(run)
    await session.flush()
    schedule.run_count += 1
    schedule.last_run_at = scheduled_for
    # Advance from wall-clock time, not from the stale occurrence, to avoid catch-up storms.
    following = next_occurrence(spec, after=now, inclusive=False)
    if schedule.max_runs is not None and schedule.run_count >= schedule.max_runs:
        following = None
    schedule.next_run_at = following
    if following is None:
        schedule.status = ScheduledAutomationStatus.COMPLETED.value
    session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            scheduled_run_id=run.id,
            event_type="misfire_skipped" if skipped else "run_queued",
            event_metadata={
                "scheduled_for": scheduled_for.isoformat(),
                "late_seconds": late_seconds,
                "revision": schedule.revision,
            },
        )
    )
    await session.commit()
    return True


async def claim_scheduled_run(
    session: AsyncSession,
    worker_id: str,
    settings: Settings,
) -> ScheduledRun | None:
    now = datetime.now(UTC)
    run = await session.scalar(
        select(ScheduledRun)
        .where(
            or_(
                (
                    ScheduledRun.status.in_(
                        [ScheduledRunStatus.QUEUED.value, ScheduledRunStatus.RETRYING.value]
                    )
                )
                & (ScheduledRun.available_at <= now),
                (ScheduledRun.status == ScheduledRunStatus.RUNNING.value)
                & (ScheduledRun.lease_expires_at < now),
            )
        )
        .order_by(ScheduledRun.available_at, ScheduledRun.scheduled_for)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if run is None:
        await session.rollback()
        return None
    concurrent = await session.scalar(
        select(func.count(ScheduledRun.id)).where(
            ScheduledRun.user_id == run.user_id,
            ScheduledRun.status == ScheduledRunStatus.RUNNING.value,
            ScheduledRun.lease_expires_at >= now,
            ScheduledRun.id != run.id,
        )
    )
    if int(concurrent or 0) >= settings.schedule_max_concurrent_runs_per_user:
        run.available_at = now + timedelta(seconds=settings.scheduler_poll_interval_seconds)
        await session.commit()
        return None
    run.status = ScheduledRunStatus.RUNNING.value
    run.attempts += 1
    run.started_at = run.started_at or now
    run.locked_by = worker_id
    run.lease_expires_at = now + timedelta(minutes=10)
    run.error_code = None
    await session.commit()
    return run


async def materialize_scheduled_run(
    session: AsyncSession,
    run: ScheduledRun,
    settings: Settings,
) -> OrchestrationTask:
    schedule = await session.get(ScheduledAutomation, run.scheduled_automation_id)
    if schedule is None or schedule.workspace_id != run.workspace_id:
        raise RuntimeError("Rotina agendada não encontrada")
    if not run.manual and schedule.status in {
        ScheduledAutomationStatus.PAUSED.value,
        ScheduledAutomationStatus.DELETED.value,
    }:
        run.status = ScheduledRunStatus.SKIPPED.value
        run.result_code = "schedule_inactive"
        run.completed_at = datetime.now(UTC)
        await session.commit()
        raise RuntimeError("Rotina foi pausada antes da materialização")
    grant = await session.scalar(
        select(AutomationGrant).where(
            AutomationGrant.scheduled_automation_id == schedule.id,
            AutomationGrant.automation_revision == run.automation_revision,
            AutomationGrant.status == "active",
        )
    )
    if grant is None:
        schedule.status = ScheduledAutomationStatus.NEEDS_ATTENTION.value
        raise RuntimeError("Autorização ativa da rotina não encontrada")
    if run.orchestration_task_id is not None:
        existing = await session.get(OrchestrationTask, run.orchestration_task_id)
        if existing is not None:
            return existing
    conversation = await session.get(Conversation, schedule.conversation_id)
    if conversation is None or conversation.status != "active":
        schedule.status = ScheduledAutomationStatus.NEEDS_ATTENTION.value
        raise RuntimeError("Conversa de entrega da rotina não está ativa")
    spec = ScheduleSpec.model_validate(schedule.compiled_spec)
    request_context = RequestContext(
        identity=Identity(user_id=schedule.user_id),
        workspace_id=schedule.workspace_id,
    )
    profile_summary = ""
    if spec.context_policy.user_profile:
        profile = await get_user_context_profile(session, request_context, settings=settings)
        profile_summary = profile.summary
    inbound = ChannelMessage(
        workspace_id=schedule.workspace_id,
        conversation_id=conversation.id,
        provider=conversation.provider,
        external_message_id=f"schedule:{run.id}",
        direction="inbound",
        content=spec.objective,
        status="completed",
        message_metadata={
            "origin": "scheduled_automation",
            "scheduled_run_id": str(run.id),
            "scheduled_automation_id": str(schedule.id),
            "scheduled_for": run.scheduled_for.isoformat(),
        },
    )
    session.add(inbound)
    await session.flush()
    task = OrchestrationTask(
        workspace_id=schedule.workspace_id,
        user_id=schedule.user_id,
        conversation_id=conversation.id,
        inbound_message_id=inbound.id,
        intent=OrchestrationIntent.AUTOMATION.value,
        request_text=spec.objective,
        summary=f"Executar rotina agendada: {schedule.name}",
        routing_context={
            "route": "scheduled",
            "scheduled_run_id": str(run.id),
            "scheduled_automation_id": str(schedule.id),
            "automation_grant_id": str(grant.id),
            "automation_revision": run.automation_revision,
            "scheduled_for": run.scheduled_for.isoformat(),
            "current_user_profile": profile_summary,
            "schedule_spec": schedule.compiled_spec,
            "confirmation_status": "none",
        },
        allowed_capabilities=schedule.capabilities_snapshot,
        status=OrchestrationTaskStatus.QUEUED.value,
        idempotency_key=f"scheduled-run:{run.id}:v1",
        max_attempts=settings.orchestration_task_max_attempts,
    )
    session.add(task)
    await session.flush()
    run.orchestration_task_id = task.id
    run.result_code = "orchestration_queued"
    session.add(
        OrchestrationTaskEvent(
            workspace_id=task.workspace_id,
            orchestration_task_id=task.id,
            event_type="created_from_schedule",
            event_metadata={"scheduled_run_id": str(run.id)},
        )
    )
    session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            scheduled_run_id=run.id,
            event_type="orchestration_queued",
            event_metadata={"orchestration_task_id": str(task.id)},
        )
    )
    await session.commit()
    return task


async def process_scheduled_run_job(
    session: AsyncSession,
    run: ScheduledRun,
    settings: Settings,
) -> None:
    run_id = run.id
    try:
        await materialize_scheduled_run(session, run, settings)
    except Exception as exc:
        await session.rollback()
        fresh = await session.get(ScheduledRun, run_id)
        if fresh is None:
            raise
        if fresh.status == ScheduledRunStatus.SKIPPED.value:
            return
        retry = fresh.attempts < fresh.max_attempts
        fresh.status = (
            ScheduledRunStatus.RETRYING.value if retry else ScheduledRunStatus.FAILED.value
        )
        fresh.available_at = datetime.now(UTC) + timedelta(seconds=2**fresh.attempts)
        fresh.locked_by = None
        fresh.lease_expires_at = None
        fresh.error_code = type(exc).__name__
        if not retry:
            fresh.completed_at = datetime.now(UTC)
            fresh.result_code = "materialization_failed"
            schedule = await session.get(ScheduledAutomation, fresh.scheduled_automation_id)
            if schedule is not None:
                schedule.status = ScheduledAutomationStatus.NEEDS_ATTENTION.value
                await _notify_materialization_failure(session, schedule, fresh)
        await session.commit()
        logger.exception("scheduled_run_materialization_failed", extra={"run_id": str(run_id)})


async def _notify_materialization_failure(
    session: AsyncSession,
    schedule: ScheduledAutomation,
    run: ScheduledRun,
) -> None:
    conversation = await session.get(Conversation, schedule.conversation_id)
    if conversation is None:
        return
    idempotency_key = f"schedule-materialization-failed:{run.id}"
    existing = await session.scalar(
        select(OutboxMessage).where(OutboxMessage.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return
    text = format_channel_text(
        conversation.provider,
        f"Não consegui executar a rotina '{schedule.name}'. "
        "Ela foi marcada como precisando de atenção. Você pode revisar ou executar novamente.",
    )
    outbound = ChannelMessage(
        workspace_id=schedule.workspace_id,
        conversation_id=conversation.id,
        provider=conversation.provider,
        direction="outbound",
        content=text,
        status="completed" if conversation.provider == API_PROVIDER else "queued",
        message_metadata={
            "response_phase": "schedule_failure",
            "scheduled_run_id": str(run.id),
            "scheduled_automation_id": str(schedule.id),
        },
    )
    session.add(outbound)
    await session.flush()
    if conversation.provider == API_PROVIDER:
        return
    destination = str(conversation.external_thread_id or "")
    if conversation.channel_account_id is not None:
        account = await session.get(ChannelAccount, conversation.channel_account_id)
        if account is not None and account.provider == conversation.provider:
            destination = account.external_account_id
    if not destination:
        return
    session.add(
        OutboxMessage(
            workspace_id=schedule.workspace_id,
            conversation_id=conversation.id,
            channel_message_id=outbound.id,
            provider=conversation.provider,
            destination=destination,
            payload={"type": "text", "text": {"body": text}},
            status="pending",
            idempotency_key=idempotency_key,
        )
    )


async def finish_scheduled_run_for_task(
    session: AsyncSession,
    task: OrchestrationTask,
    *,
    success: bool,
    error_code: str | None = None,
) -> None:
    raw_run_id = task.routing_context.get("scheduled_run_id")
    if not raw_run_id:
        return
    try:
        run_id = uuid.UUID(str(raw_run_id))
    except ValueError:
        return
    run = await session.get(ScheduledRun, run_id)
    if run is None:
        return
    now = datetime.now(UTC)
    run.status = ScheduledRunStatus.COMPLETED.value if success else ScheduledRunStatus.FAILED.value
    run.result_code = "completed" if success else "orchestration_failed"
    run.error_code = error_code
    run.completed_at = now
    run.locked_by = None
    run.lease_expires_at = None
    session.add(
        ScheduleEvent(
            workspace_id=run.workspace_id,
            scheduled_automation_id=run.scheduled_automation_id,
            scheduled_run_id=run.id,
            event_type="completed" if success else "failed",
            event_metadata={"error_code": error_code} if error_code else {},
        )
    )
