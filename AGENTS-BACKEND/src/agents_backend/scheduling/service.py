from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from agents_backend.conversation.tools import (
    ToolContext,
    ToolEnvelope,
    ToolSpec,
    _failure,
    _success,
    create_pending_action,
)
from agents_backend.integrations.composio.policies import POLICIES
from agents_backend.models import (
    AutomationGrant,
    ExternalIntegration,
    ScheduledAutomation,
    ScheduledAutomationStatus,
    ScheduledRun,
    ScheduleEvent,
)
from agents_backend.scheduling.recurrence import next_occurrence
from agents_backend.scheduling.schemas import (
    CreateScheduleArguments,
    ListScheduleRunsArguments,
    ListSchedulesArguments,
    ScheduleIdentifierArguments,
    ScheduleSpec,
    UpdateScheduleArguments,
)

LOCAL_SCHEDULE_TOOLS: dict[str, str] = {
    "deliver_to_user": "R0",
    "search_memory": "R0",
    "get_entity": "R0",
    "list_open_commitments": "R0",
}
SCHEDULE_TOOL_RISKS = {
    **LOCAL_SCHEDULE_TOOLS,
    **{policy.name: policy.risk for policy in POLICIES},
}
DISALLOWED_STANDING_TOOLS = {
    "calendar_delete_event",
}
RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2}


def _spec_data(spec: ScheduleSpec) -> dict[str, Any]:
    return spec.model_dump(mode="json")


def _schedule_data(schedule: ScheduledAutomation) -> dict[str, Any]:
    return {
        "schedule_id": str(schedule.id),
        "name": schedule.name,
        "objective": schedule.compiled_spec.get("objective"),
        "status": schedule.status,
        "timezone": schedule.timezone,
        "recurrence_rule": schedule.recurrence_rule,
        "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        "run_count": schedule.run_count,
        "max_runs": schedule.max_runs,
        "revision": schedule.revision,
        "tools": schedule.tool_policy_snapshot.get("tools", []),
    }


def _validate_tool_policy(spec: ScheduleSpec) -> str | None:
    unknown = [name for name in spec.tool_policy.tools if name not in SCHEDULE_TOOL_RISKS]
    if unknown:
        return f"Tools ainda não aprovadas para agendamento: {', '.join(unknown)}"
    disallowed = DISALLOWED_STANDING_TOOLS.intersection(spec.tool_policy.tools)
    if disallowed:
        return f"Tools destrutivas não podem ter autorização recorrente: {', '.join(disallowed)}"
    memory_tools = {"search_memory", "get_entity", "list_open_commitments"}.intersection(
        spec.tool_policy.tools
    )
    if memory_tools and not spec.context_policy.long_term_memory:
        return "Tools de memória exigem long_term_memory=true"
    if "search_memory" in memory_tools and spec.context_policy.maximum_memory_queries == 0:
        return "search_memory exige maximum_memory_queries maior que zero"
    required_risk = max(RISK_ORDER[SCHEDULE_TOOL_RISKS[name]] for name in spec.tool_policy.tools)
    if RISK_ORDER[spec.tool_policy.max_risk] < required_risk:
        return "max_risk é menor que o risco das tools solicitadas"
    if required_risk >= RISK_ORDER["R2"]:
        if spec.tool_policy.account_scope != "specific_accounts":
            return "Escritas recorrentes R2 exigem contas específicas"
        constraints = spec.tool_policy.constraints
        if "gmail_send_email" in spec.tool_policy.tools and not constraints.recipient_emails:
            return "Envio recorrente de email exige recipient_emails fixos"
        if "whatsapp_send_message" in spec.tool_policy.tools and not constraints.to_numbers:
            return "Envio recorrente de mensagem exige to_numbers fixos"
    return None


def _capabilities_for_spec(spec: ScheduleSpec) -> list[str]:
    capabilities = {"schedule_execution"}
    if any(name in LOCAL_SCHEDULE_TOOLS for name in spec.tool_policy.tools):
        capabilities.add("memory_read")
    external = [policy for policy in POLICIES if policy.name in set(spec.tool_policy.tools)]
    capabilities.update(policy.capability for policy in external)
    return sorted(capabilities)


async def _validate_accounts(context: ToolContext, spec: ScheduleSpec) -> str | None:
    account_ids = spec.tool_policy.account_ids
    if not account_ids:
        return None
    integrations = list(
        (
            await context.session.scalars(
                select(ExternalIntegration).where(
                    ExternalIntegration.id.in_(account_ids),
                    ExternalIntegration.workspace_id == context.request_context.workspace_id,
                    ExternalIntegration.user_id == context.request_context.identity.user_id,
                    ExternalIntegration.status == "active",
                )
            )
        ).all()
    )
    if {item.id for item in integrations} != set(account_ids):
        return "Uma ou mais contas selecionadas não pertencem ao usuário ou não estão ativas"
    required_toolkits = {
        policy.toolkit for policy in POLICIES if policy.name in set(spec.tool_policy.tools)
    }
    connected_toolkits = {item.toolkit_slug for item in integrations}
    missing = sorted(required_toolkits - connected_toolkits)
    if missing:
        return f"Falta selecionar uma conta para: {', '.join(missing)}"
    return None


def _confirmation_summary(schedule: ScheduledAutomation) -> str:
    tools = ", ".join(schedule.tool_policy_snapshot.get("tools", []))
    next_run = schedule.next_run_at.isoformat() if schedule.next_run_at else "sem ocorrência"
    return (
        f"Ativar rotina '{schedule.name}'. Próxima execução: {next_run}. "
        f"Tools autorizadas: {tools}. A autorização valerá para execuções futuras desta revisão."
    )


def _can_activate_from_initial_request(spec: ScheduleSpec) -> bool:
    return (
        spec.trigger.kind == "once"
        and spec.tool_policy.tools == ["deliver_to_user"]
        and spec.tool_policy.max_risk == "R0"
        and not spec.tool_policy.account_ids
        and spec.delivery.kind == "originating_conversation"
    )


async def _scoped_schedule(
    context: ToolContext,
    schedule_id: uuid.UUID,
    *,
    lock: bool = False,
) -> ScheduledAutomation | None:
    statement = select(ScheduledAutomation).where(
        ScheduledAutomation.id == schedule_id,
        ScheduledAutomation.workspace_id == context.request_context.workspace_id,
        ScheduledAutomation.user_id == context.request_context.identity.user_id,
    )
    if lock:
        statement = statement.with_for_update()
    return await context.session.scalar(statement)


async def create_schedule(context: ToolContext, arguments: CreateScheduleArguments) -> ToolEnvelope:
    spec = arguments.spec
    policy_error = _validate_tool_policy(spec)
    if policy_error:
        return _failure("schedule_policy_rejected", policy_error)
    account_error = await _validate_accounts(context, spec)
    if account_error:
        return _failure("schedule_account_rejected", account_error)
    now = datetime.now(UTC)
    activate_immediately = _can_activate_from_initial_request(spec)
    next_run = next_occurrence(spec, after=now, inclusive=True)
    if next_run is None and activate_immediately:
        intended_at = spec.trigger.starts_at.astimezone(UTC)
        late_seconds = (now - intended_at).total_seconds()
        if 0 <= late_seconds <= spec.misfire_grace_seconds:
            next_run = now
    if next_run is None:
        return _failure(
            "schedule_has_no_future_occurrence",
            "O agendamento não possui uma próxima ocorrência futura.",
        )
    schedule = ScheduledAutomation(
        workspace_id=context.request_context.workspace_id,
        user_id=context.request_context.identity.user_id,
        conversation_id=context.conversation.id,
        name=spec.name,
        original_request=context.inbound_message.content,
        compiled_spec=_spec_data(spec),
        timezone=spec.trigger.timezone,
        recurrence_rule=spec.trigger.recurrence_rule,
        starts_at=spec.trigger.starts_at.astimezone(UTC),
        ends_at=spec.trigger.ends_at.astimezone(UTC) if spec.trigger.ends_at else None,
        next_run_at=next_run,
        status=(
            ScheduledAutomationStatus.ACTIVE.value
            if activate_immediately
            else ScheduledAutomationStatus.AWAITING_CONFIRMATION.value
        ),
        misfire_policy=spec.misfire_policy,
        misfire_grace_seconds=spec.misfire_grace_seconds,
        max_runs=spec.max_runs,
        capabilities_snapshot=_capabilities_for_spec(spec),
        tool_policy_snapshot=spec.tool_policy.model_dump(mode="json"),
        activated_at=now if activate_immediately else None,
    )
    context.session.add(schedule)
    await context.session.flush()
    grant = AutomationGrant(
        workspace_id=schedule.workspace_id,
        user_id=schedule.user_id,
        scheduled_automation_id=schedule.id,
        automation_revision=schedule.revision,
        allowed_tools=spec.tool_policy.tools,
        allowed_account_ids=[str(value) for value in spec.tool_policy.account_ids],
        constraints=spec.tool_policy.constraints.model_dump(mode="json"),
        max_risk=spec.tool_policy.max_risk,
        status="active" if activate_immediately else "pending",
        confirmed_by_message_id=context.inbound_message.id if activate_immediately else None,
        confirmed_at=now if activate_immediately else None,
    )
    context.session.add(grant)
    await context.session.flush()
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="created",
            event_metadata={"revision": schedule.revision},
        )
    )
    if activate_immediately:
        context.session.add(
            ScheduleEvent(
                workspace_id=schedule.workspace_id,
                scheduled_automation_id=schedule.id,
                event_type="activated",
                event_metadata={
                    "revision": schedule.revision,
                    "grant_id": str(grant.id),
                    "confirmation_mode": "explicit_initial_request",
                },
            )
        )
        return _success(
            "schedule_activated",
            "O aviso pontual foi programado e já está ativo.",
            _schedule_data(schedule),
        )
    pending = await create_pending_action(
        context,
        tool_name="activate_schedule",
        arguments={"schedule_id": str(schedule.id), "revision": schedule.revision},
        summary=_confirmation_summary(schedule),
    )
    return _success(
        "confirmation_required",
        "A rotina foi preparada, mas ainda não está ativa. Confirme em uma nova mensagem.",
        {"schedule": _schedule_data(schedule), "confirmation": pending.summary},
    )


async def activate_pending_schedule(
    context: ToolContext,
    *,
    schedule_id: uuid.UUID,
    revision: int,
    confirmation_message_id: uuid.UUID,
) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, schedule_id, lock=True)
    if schedule is None or schedule.revision != revision:
        return _failure("schedule_not_found", "A rotina pendente não foi encontrada.")
    grant = await context.session.scalar(
        select(AutomationGrant).where(
            AutomationGrant.scheduled_automation_id == schedule.id,
            AutomationGrant.automation_revision == revision,
            AutomationGrant.status == "pending",
        )
    )
    if grant is None:
        return _failure("schedule_grant_not_found", "A autorização da rotina não foi encontrada.")
    spec = ScheduleSpec.model_validate(schedule.compiled_spec)
    now = datetime.now(UTC)
    next_run = next_occurrence(spec, after=now, inclusive=True)
    if next_run is None:
        intended_at = spec.trigger.starts_at.astimezone(UTC)
        late_seconds = (now - intended_at).total_seconds()
        if spec.trigger.kind == "once" and 0 <= late_seconds <= schedule.misfire_grace_seconds:
            next_run = now
        else:
            grant.status = "revoked"
            grant.revoked_at = now
            schedule.status = ScheduledAutomationStatus.EXPIRED.value
            schedule.next_run_at = None
            context.session.add(
                ScheduleEvent(
                    workspace_id=schedule.workspace_id,
                    scheduled_automation_id=schedule.id,
                    event_type="expired",
                    event_metadata={"revision": revision, "reason": "confirmation_too_late"},
                )
            )
            return _failure(
                "schedule_has_no_future_occurrence",
                "O horário da rotina passou além da tolerância e ela expirou.",
            )
    grant.status = "active"
    grant.confirmed_by_message_id = confirmation_message_id
    grant.confirmed_at = now
    schedule.status = ScheduledAutomationStatus.ACTIVE.value
    schedule.next_run_at = next_run
    schedule.activated_at = now
    schedule.paused_at = None
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="activated",
            event_metadata={"revision": revision, "grant_id": str(grant.id)},
        )
    )
    return _success(
        "schedule_activated",
        "A rotina foi ativada.",
        _schedule_data(schedule),
    )


async def cancel_pending_schedule(
    context: ToolContext,
    *,
    schedule_id: uuid.UUID,
    revision: int,
) -> None:
    schedule = await _scoped_schedule(context, schedule_id, lock=True)
    if schedule is None or schedule.revision != revision:
        return
    now = datetime.now(UTC)
    schedule.status = ScheduledAutomationStatus.DELETED.value
    schedule.next_run_at = None
    schedule.deleted_at = now
    grant = await context.session.scalar(
        select(AutomationGrant).where(
            AutomationGrant.scheduled_automation_id == schedule.id,
            AutomationGrant.automation_revision == revision,
            AutomationGrant.status == "pending",
        )
    )
    if grant is not None:
        grant.status = "revoked"
        grant.revoked_at = now
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="activation_cancelled",
            event_metadata={"revision": revision},
        )
    )


async def list_schedules(context: ToolContext, arguments: ListSchedulesArguments) -> ToolEnvelope:
    statement = select(ScheduledAutomation).where(
        ScheduledAutomation.workspace_id == context.request_context.workspace_id,
        ScheduledAutomation.user_id == context.request_context.identity.user_id,
    )
    if not arguments.include_inactive:
        statement = statement.where(
            ScheduledAutomation.status.notin_(
                [ScheduledAutomationStatus.DELETED.value, ScheduledAutomationStatus.COMPLETED.value]
            )
        )
    schedules = list(
        (
            await context.session.scalars(
                statement.order_by(ScheduledAutomation.created_at.desc()).limit(arguments.limit)
            )
        ).all()
    )
    return _success(
        "schedules_listed",
        f"Foram encontradas {len(schedules)} rotina(s).",
        [_schedule_data(schedule) for schedule in schedules],
    )


async def get_schedule(
    context: ToolContext, arguments: ScheduleIdentifierArguments
) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, arguments.schedule_id)
    if schedule is None:
        return _failure("schedule_not_found", "A rotina não foi encontrada.")
    return _success("schedule_found", "A rotina foi encontrada.", _schedule_data(schedule))


async def pause_schedule(
    context: ToolContext, arguments: ScheduleIdentifierArguments
) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, arguments.schedule_id, lock=True)
    if schedule is None:
        return _failure("schedule_not_found", "A rotina não foi encontrada.")
    if schedule.status != ScheduledAutomationStatus.ACTIVE.value:
        return _failure("schedule_not_active", "A rotina não está ativa.")
    schedule.status = ScheduledAutomationStatus.PAUSED.value
    schedule.paused_at = datetime.now(UTC)
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="paused",
            event_metadata={"revision": schedule.revision},
        )
    )
    return _success("schedule_paused", "A rotina foi pausada.", _schedule_data(schedule))


async def resume_schedule(
    context: ToolContext, arguments: ScheduleIdentifierArguments
) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, arguments.schedule_id, lock=True)
    if schedule is None:
        return _failure("schedule_not_found", "A rotina não foi encontrada.")
    grant = await context.session.scalar(
        select(AutomationGrant).where(
            AutomationGrant.scheduled_automation_id == schedule.id,
            AutomationGrant.automation_revision == schedule.revision,
            AutomationGrant.status == "active",
        )
    )
    if grant is None:
        return _failure(
            "schedule_confirmation_required",
            "A autorização desta revisão não está ativa; atualize e confirme a rotina.",
        )
    spec = ScheduleSpec.model_validate(schedule.compiled_spec)
    schedule.next_run_at = next_occurrence(spec, after=datetime.now(UTC), inclusive=True)
    if schedule.next_run_at is None:
        schedule.status = ScheduledAutomationStatus.COMPLETED.value
        return _failure("schedule_has_no_future_occurrence", "A rotina não tem ocorrência futura.")
    schedule.status = ScheduledAutomationStatus.ACTIVE.value
    schedule.paused_at = None
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="resumed",
            event_metadata={"revision": schedule.revision},
        )
    )
    return _success("schedule_resumed", "A rotina foi retomada.", _schedule_data(schedule))


async def delete_schedule(
    context: ToolContext, arguments: ScheduleIdentifierArguments
) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, arguments.schedule_id, lock=True)
    if schedule is None:
        return _failure("schedule_not_found", "A rotina não foi encontrada.")
    now = datetime.now(UTC)
    schedule.status = ScheduledAutomationStatus.DELETED.value
    schedule.deleted_at = now
    schedule.next_run_at = None
    grants = list(
        (
            await context.session.scalars(
                select(AutomationGrant).where(
                    AutomationGrant.scheduled_automation_id == schedule.id,
                    AutomationGrant.status.in_(["pending", "active"]),
                )
            )
        ).all()
    )
    for grant in grants:
        grant.status = "revoked"
        grant.revoked_at = now
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="deleted",
            event_metadata={"revision": schedule.revision},
        )
    )
    return _success("schedule_deleted", "A rotina foi removida.", _schedule_data(schedule))


async def run_schedule_now(
    context: ToolContext, arguments: ScheduleIdentifierArguments
) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, arguments.schedule_id)
    if schedule is None:
        return _failure("schedule_not_found", "A rotina não foi encontrada.")
    if schedule.status not in {
        ScheduledAutomationStatus.ACTIVE.value,
        ScheduledAutomationStatus.PAUSED.value,
    }:
        return _failure("schedule_not_authorized", "A rotina não possui autorização ativa.")
    grant = await context.session.scalar(
        select(AutomationGrant).where(
            AutomationGrant.scheduled_automation_id == schedule.id,
            AutomationGrant.automation_revision == schedule.revision,
            AutomationGrant.status == "active",
        )
    )
    if grant is None:
        return _failure("schedule_not_authorized", "A rotina não possui autorização ativa.")
    run = ScheduledRun(
        workspace_id=schedule.workspace_id,
        user_id=schedule.user_id,
        scheduled_automation_id=schedule.id,
        automation_revision=schedule.revision,
        scheduled_for=datetime.now(UTC),
        status="queued",
        manual=True,
        max_attempts=context.settings.schedule_max_run_attempts,
    )
    context.session.add(run)
    await context.session.flush()
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            scheduled_run_id=run.id,
            event_type="manual_run_queued",
            event_metadata={"revision": schedule.revision},
        )
    )
    return _success(
        "schedule_run_queued",
        "A rotina foi colocada na fila para execução imediata.",
        {"schedule_id": str(schedule.id), "run_id": str(run.id)},
    )


async def list_schedule_runs(
    context: ToolContext, arguments: ListScheduleRunsArguments
) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, arguments.schedule_id)
    if schedule is None:
        return _failure("schedule_not_found", "A rotina não foi encontrada.")
    runs = list(
        (
            await context.session.scalars(
                select(ScheduledRun)
                .where(ScheduledRun.scheduled_automation_id == schedule.id)
                .order_by(ScheduledRun.scheduled_for.desc())
                .limit(arguments.limit)
            )
        ).all()
    )
    return _success(
        "schedule_runs_listed",
        f"Foram encontradas {len(runs)} execução(ões).",
        [
            {
                "run_id": str(run.id),
                "scheduled_for": run.scheduled_for.isoformat(),
                "status": run.status,
                "manual": run.manual,
                "attempts": run.attempts,
                "result_code": run.result_code,
                "error_code": run.error_code,
            }
            for run in runs
        ],
    )


async def update_schedule(context: ToolContext, arguments: UpdateScheduleArguments) -> ToolEnvelope:
    schedule = await _scoped_schedule(context, arguments.schedule_id, lock=True)
    if schedule is None:
        return _failure("schedule_not_found", "A rotina não foi encontrada.")
    policy_error = _validate_tool_policy(arguments.spec)
    if policy_error:
        return _failure("schedule_policy_rejected", policy_error)
    account_error = await _validate_accounts(context, arguments.spec)
    if account_error:
        return _failure("schedule_account_rejected", account_error)
    next_run = next_occurrence(arguments.spec, after=datetime.now(UTC), inclusive=True)
    if next_run is None:
        return _failure("schedule_has_no_future_occurrence", "A rotina não tem ocorrência futura.")
    now = datetime.now(UTC)
    old_grants = list(
        (
            await context.session.scalars(
                select(AutomationGrant).where(
                    AutomationGrant.scheduled_automation_id == schedule.id,
                    AutomationGrant.status.in_(["active", "pending"]),
                )
            )
        ).all()
    )
    for old in old_grants:
        old.status = "revoked"
        old.revoked_at = now
    spec = arguments.spec
    schedule.revision += 1
    schedule.name = spec.name
    schedule.original_request = context.inbound_message.content
    schedule.compiled_spec = _spec_data(spec)
    schedule.timezone = spec.trigger.timezone
    schedule.recurrence_rule = spec.trigger.recurrence_rule
    schedule.starts_at = spec.trigger.starts_at.astimezone(UTC)
    schedule.ends_at = spec.trigger.ends_at.astimezone(UTC) if spec.trigger.ends_at else None
    schedule.next_run_at = next_run
    schedule.status = ScheduledAutomationStatus.AWAITING_CONFIRMATION.value
    schedule.misfire_policy = spec.misfire_policy
    schedule.misfire_grace_seconds = spec.misfire_grace_seconds
    schedule.max_runs = spec.max_runs
    schedule.capabilities_snapshot = _capabilities_for_spec(spec)
    schedule.tool_policy_snapshot = spec.tool_policy.model_dump(mode="json")
    grant = AutomationGrant(
        workspace_id=schedule.workspace_id,
        user_id=schedule.user_id,
        scheduled_automation_id=schedule.id,
        automation_revision=schedule.revision,
        allowed_tools=spec.tool_policy.tools,
        allowed_account_ids=[str(value) for value in spec.tool_policy.account_ids],
        constraints=spec.tool_policy.constraints.model_dump(mode="json"),
        max_risk=spec.tool_policy.max_risk,
        status="pending",
    )
    context.session.add(grant)
    context.session.add(
        ScheduleEvent(
            workspace_id=schedule.workspace_id,
            scheduled_automation_id=schedule.id,
            event_type="updated",
            event_metadata={"revision": schedule.revision},
        )
    )
    pending = await create_pending_action(
        context,
        tool_name="activate_schedule",
        arguments={"schedule_id": str(schedule.id), "revision": schedule.revision},
        summary=_confirmation_summary(schedule),
    )
    return _success(
        "confirmation_required",
        "A nova versão foi preparada. Confirme em uma nova mensagem para ativá-la.",
        {"schedule": _schedule_data(schedule), "confirmation": pending.summary},
    )


def schedule_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "create_schedule",
            "Cria uma rotina pontual ou recorrente. Um aviso único para o próprio chat usando "
            "somente deliver_to_user é ativado imediatamente; outras rotinas exigem confirmação "
            "única. Use datas explícitas com offset e RRULE para recorrência.",
            CreateScheduleArguments,
            "R2",
            create_schedule,
        ),
        ToolSpec(
            "list_schedules",
            "Lista rotinas do usuário, estados e próximas execuções.",
            ListSchedulesArguments,
            "R0",
            list_schedules,
        ),
        ToolSpec(
            "get_schedule",
            "Consulta uma rotina por schedule_id.",
            ScheduleIdentifierArguments,
            "R0",
            get_schedule,
        ),
        ToolSpec(
            "update_schedule",
            "Prepara uma nova versão de uma rotina e exige confirmação para reativá-la.",
            UpdateScheduleArguments,
            "R2",
            update_schedule,
        ),
        ToolSpec(
            "pause_schedule",
            "Pausa uma rotina ativa.",
            ScheduleIdentifierArguments,
            "R1",
            pause_schedule,
        ),
        ToolSpec(
            "resume_schedule",
            "Retoma uma rotina pausada que ainda possui autorização válida.",
            ScheduleIdentifierArguments,
            "R1",
            resume_schedule,
        ),
        ToolSpec(
            "delete_schedule",
            "Remove logicamente uma rotina e revoga sua autorização.",
            ScheduleIdentifierArguments,
            "R1",
            delete_schedule,
        ),
        ToolSpec(
            "run_schedule_now",
            "Executa imediatamente uma rotina já autorizada.",
            ScheduleIdentifierArguments,
            "R1",
            run_schedule_now,
        ),
        ToolSpec(
            "list_schedule_runs",
            "Lista execuções recentes de uma rotina.",
            ListScheduleRunsArguments,
            "R0",
            list_schedule_runs,
        ),
    ]
