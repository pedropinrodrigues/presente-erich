from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from agents_backend.auth import RequestContext
from agents_backend.config import Settings
from agents_backend.conversation.tools import (
    ToolContext,
    ToolRegistry,
    orchestration_tool_specs,
)
from agents_backend.models import (
    AgentRun,
    AutomationGrant,
    ChannelMessage,
    Conversation,
    ExternalIntegration,
    OrchestrationTask,
    PendingAction,
    ScheduledAutomation,
    ScheduledRun,
)
from agents_backend.scheduling.dispatcher import (
    _should_skip_misfire,
    claim_scheduled_run,
    dispatch_due_schedule,
    expire_stale_schedules,
    materialize_scheduled_run,
)
from agents_backend.scheduling.recurrence import next_occurrence
from agents_backend.scheduling.schemas import (
    CreateScheduleArguments,
    ScheduleSpec,
    ScheduleTrigger,
    UpdateScheduleArguments,
)
from agents_backend.scheduling.service import (
    _capabilities_for_spec,
    _validate_tool_policy,
    activate_pending_schedule,
    cancel_pending_schedule,
    create_schedule,
    renew_expired_schedule_confirmation,
    schedule_tool_specs,
    update_schedule,
)


def schedule_settings() -> Settings:
    return Settings(
        _env_file=None,
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_ANON_KEY="anon",
        SUPABASE_SERVICE_ROLE_KEY="service-role",
        DATABASE_URL="postgresql://postgres:password@db.project.supabase.co/postgres",
        OPENAI_API_KEY="openai-test",
        OPENAI_MODEL_EXTRACTION="gpt-5.6-luna",
        OPENAI_MODEL_ANSWERING="gpt-5.6-luna",
    )  # type: ignore[arg-type]


def daily_spec(start: datetime | None = None) -> ScheduleSpec:
    return ScheduleSpec.model_validate(
        {
            "name": "Briefing diário",
            "objective": "Resuma meus compromissos de hoje com contexto da memória.",
            "trigger": {
                "kind": "recurring",
                "timezone": "America/Sao_Paulo",
                "starts_at": (start or datetime(2099, 1, 1, 7, 30, tzinfo=UTC)).isoformat(),
                "recurrence_rule": "FREQ=DAILY",
                "ends_at": None,
            },
            "context_policy": {
                "user_profile": True,
                "long_term_memory": True,
                "maximum_memory_queries": 8,
            },
            "tool_policy": {
                "tools": ["calendar_list_events", "search_memory", "deliver_to_user"],
                "account_scope": "all_connected_accounts",
                "account_ids": [],
                "max_risk": "R0",
                "constraints": {},
            },
            "delivery": {"kind": "originating_conversation", "send_when_empty": True},
            "misfire_policy": "latest",
            "misfire_grace_seconds": 21600,
            "max_runs": None,
        }
    )


def reminder_spec(start: datetime, *, include_calendar: bool = False) -> ScheduleSpec:
    tools = ["calendar_list_events", "deliver_to_user"] if include_calendar else ["deliver_to_user"]
    return ScheduleSpec.model_validate(
        {
            "name": "Aviso em 1 minuto",
            "objective": 'Envie ao usuário: "Lembrete de teste".',
            "trigger": {
                "kind": "once",
                "timezone": "America/Sao_Paulo",
                "starts_at": start.isoformat(),
                "recurrence_rule": None,
                "ends_at": None,
            },
            "context_policy": {
                "user_profile": False,
                "long_term_memory": False,
                "maximum_memory_queries": 0,
            },
            "tool_policy": {
                "tools": tools,
                "account_scope": "primary",
                "account_ids": [],
                "max_risk": "R0",
                "constraints": {
                    "recipient_emails": [],
                    "to_numbers": [],
                    "maximum_external_writes_per_run": 0,
                    "allow_attachments": False,
                },
            },
            "delivery": {"kind": "originating_conversation", "send_when_empty": True},
            "misfire_policy": "latest",
            "misfire_grace_seconds": 60,
            "max_runs": 1,
        }
    )


def web_research_spec(start: datetime | None = None) -> ScheduleSpec:
    data = daily_spec(start).model_dump(mode="json")
    data["name"] = "Notícias diárias de tecnologia"
    data["objective"] = "Pesquise as principais notícias de tecnologia e entregue com fontes."
    data["context_policy"] = {
        "user_profile": False,
        "long_term_memory": False,
        "maximum_memory_queries": 0,
    }
    data["tool_policy"] = {
        "tools": ["research_web", "deliver_to_user"],
        "account_scope": "primary",
        "account_ids": [],
        "max_risk": "R0",
        "constraints": {
            "recipient_emails": [],
            "to_numbers": [],
            "maximum_external_writes_per_run": 0,
            "allow_attachments": False,
        },
    }
    return ScheduleSpec.model_validate(data)


def r2_schedule_spec(account_id: uuid.UUID, start: datetime | None = None) -> ScheduleSpec:
    data = daily_spec(start).model_dump(mode="json")
    data["name"] = "Agenda recorrente com escrita externa"
    data["objective"] = "Crie no calendário o evento definido pelo pedido do usuário."
    data["tool_policy"] = {
        "tools": ["calendar_create_event", "deliver_to_user"],
        "account_scope": "specific_accounts",
        "account_ids": [str(account_id)],
        "max_risk": "R2",
        "constraints": {
            "recipient_emails": [],
            "to_numbers": [],
            "maximum_external_writes_per_run": 1,
            "allow_attachments": False,
        },
    }
    return ScheduleSpec.model_validate(data)


def r2_once_schedule_spec(account_id: uuid.UUID, start: datetime) -> ScheduleSpec:
    data = reminder_spec(start).model_dump(mode="json")
    data["name"] = "Criação pontual no calendário"
    data["objective"] = "Crie no calendário o evento definido pelo pedido do usuário."
    data["tool_policy"] = {
        "tools": ["calendar_create_event", "deliver_to_user"],
        "account_scope": "specific_accounts",
        "account_ids": [str(account_id)],
        "max_risk": "R2",
        "constraints": {
            "recipient_emails": [],
            "to_numbers": [],
            "maximum_external_writes_per_run": 1,
            "allow_attachments": False,
        },
    }
    return ScheduleSpec.model_validate(data)


async def connected_calendar(session, context: RequestContext) -> ExternalIntegration:
    integration = ExternalIntegration(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="composio",
        toolkit_slug="googlecalendar",
        auth_config_id="calendar-auth",
        connected_account_id=f"calendar-{uuid.uuid4()}",
        status="active",
        is_default=True,
        integration_metadata={},
    )
    session.add(integration)
    await session.flush()
    return integration


async def records(session, context: RequestContext, content: str):
    conversation = Conversation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="api",
        status="active",
        conversation_metadata={},
    )
    session.add(conversation)
    await session.flush()
    inbound = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="api",
        external_message_id=f"test:{uuid.uuid4()}",
        direction="inbound",
        content=content,
        status="processing",
        message_metadata={},
    )
    session.add(inbound)
    await session.flush()
    run = AgentRun(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        inbound_message_id=inbound.id,
        model="test",
        prompt_version="test",
        status="running",
    )
    session.add(run)
    await session.flush()
    return conversation, inbound, run


def test_recurrence_returns_next_occurrence_and_rejects_sub_five_minutes() -> None:
    spec = daily_spec(datetime(2026, 8, 24, 10, 30, tzinfo=UTC))

    result = next_occurrence(
        spec,
        after=datetime(2026, 8, 24, 11, tzinfo=UTC),
    )

    assert result == datetime(2026, 8, 25, 10, 30, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ScheduleTrigger(
            kind="recurring",
            timezone="UTC",
            starts_at=datetime(2026, 8, 24, tzinfo=UTC),
            recurrence_rule="FREQ=MINUTELY;INTERVAL=1",
            ends_at=None,
        )


def test_schedule_tool_schemas_are_strict() -> None:
    definitions = ToolRegistry(schedule_tool_specs()).definitions()

    for definition in definitions:
        schema = definition["parameters"]
        assert definition["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for nested in schema.get("$defs", {}).values():
            if nested.get("type") == "object":
                assert nested["additionalProperties"] is False


def test_misfire_policy_tolerates_polling_jitter_and_respects_grace() -> None:
    assert not _should_skip_misfire(
        late_seconds=2,
        policy="skip",
        grace_seconds=3600,
        poll_interval_seconds=1,
    )
    assert not _should_skip_misfire(
        late_seconds=2,
        policy="skip",
        grace_seconds=0,
        poll_interval_seconds=1,
    )
    assert _should_skip_misfire(
        late_seconds=10,
        policy="skip",
        grace_seconds=3600,
        poll_interval_seconds=1,
    )
    assert not _should_skip_misfire(
        late_seconds=3599,
        policy="latest",
        grace_seconds=3600,
        poll_interval_seconds=1,
    )
    assert _should_skip_misfire(
        late_seconds=3601,
        policy="latest",
        grace_seconds=3600,
        poll_interval_seconds=1,
    )


def test_schedule_memory_policy_cannot_bypass_context_limits() -> None:
    data = daily_spec().model_dump(mode="json")
    data["context_policy"]["long_term_memory"] = False

    assert _validate_tool_policy(ScheduleSpec.model_validate(data)) == (
        "Tools de memória exigem long_term_memory=true"
    )

    data["context_policy"]["long_term_memory"] = True
    data["context_policy"]["maximum_memory_queries"] = 0
    assert _validate_tool_policy(ScheduleSpec.model_validate(data)) == (
        "search_memory exige maximum_memory_queries maior que zero"
    )


def test_web_research_is_allowed_and_scoped_for_schedules() -> None:
    spec = web_research_spec()

    assert _validate_tool_policy(spec) is None
    assert _capabilities_for_spec(spec) == ["schedule_execution", "web_research"]
    names = {
        definition["name"]
        for definition in ToolRegistry(
            orchestration_tool_specs(_capabilities_for_spec(spec))
        ).definitions()
    }
    assert names == {"research_web"}


@pytest.mark.asyncio
async def test_web_research_schedule_activates_without_second_confirmation(
    session, context
) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(
        session,
        context,
        "Todo dia pesquise as principais notícias de tecnologia e me envie com as fontes.",
    )
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="create-web-research-schedule",
        idempotency_key="create-web-research-schedule-key",
        settings=settings,
    )

    result = await create_schedule(
        tool_context,
        CreateScheduleArguments(spec=web_research_spec()),
    )
    schedule = await session.scalar(select(ScheduledAutomation))
    grant = await session.scalar(select(AutomationGrant))

    assert result.code == "schedule_activated"
    assert schedule is not None
    assert schedule.status == "active"
    assert schedule.capabilities_snapshot == ["schedule_execution", "web_research"]
    assert schedule.tool_policy_snapshot["tools"] == ["research_web", "deliver_to_user"]
    assert grant is not None
    assert grant.allowed_tools == ["research_web", "deliver_to_user"]
    assert grant.status == "active"
    assert grant.confirmed_by_message_id == inbound.id
    assert await session.scalar(select(func.count(PendingAction.id))) == 0


@pytest.mark.asyncio
async def test_one_time_chat_reminder_activates_without_second_confirmation(
    session, context
) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(
        session,
        context,
        "Mande uma mensagem para mim daqui a 1 minuto.",
    )
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="create-reminder",
        idempotency_key="create-reminder-key",
        settings=settings,
    )

    result = await create_schedule(
        tool_context,
        CreateScheduleArguments(spec=reminder_spec(datetime.now(UTC) - timedelta(seconds=1))),
    )
    schedule = await session.scalar(select(ScheduledAutomation))
    grant = await session.scalar(select(AutomationGrant))

    assert result.code == "schedule_activated"
    assert schedule is not None and schedule.status == "active"
    assert grant is not None and grant.status == "active"
    assert grant.confirmed_by_message_id == inbound.id
    assert await session.scalar(select(func.count(PendingAction.id))) == 0
    assert await dispatch_due_schedule(session, settings) is True
    assert await session.scalar(select(func.count(ScheduledRun.id))) == 1


@pytest.mark.asyncio
async def test_r2_schedule_requires_one_confirmation_then_activates(session, context) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(
        session,
        context,
        "Todo dia às 7h30 me envie meu briefing.",
    )
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="create-schedule",
        idempotency_key="create-schedule-key",
        settings=settings,
    )

    integration = await connected_calendar(session, context)
    proposed = await create_schedule(
        tool_context,
        CreateScheduleArguments(spec=r2_schedule_spec(integration.id)),
    )
    assert proposed.code == "confirmation_required"
    schedule = await session.scalar(select(ScheduledAutomation))
    assert schedule is not None
    assert schedule.status == "awaiting_confirmation"
    assert await session.scalar(select(func.count(PendingAction.id))) == 1

    confirmation = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="api",
        external_message_id=f"test:{uuid.uuid4()}",
        direction="inbound",
        content="Confirmo essa rotina.",
        status="processing",
        message_metadata={},
    )
    session.add(confirmation)
    await session.flush()
    confirmation_run = AgentRun(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        inbound_message_id=confirmation.id,
        model="test",
        prompt_version="test",
        status="running",
    )
    session.add(confirmation_run)
    await session.flush()
    registry = ToolRegistry()

    result = await registry.execute(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=confirmation,
        agent_run=confirmation_run,
        call_id="confirm-schedule",
        tool_name="confirm_action",
        raw_arguments=json.dumps({"action_id": None}),
        settings=settings,
    )
    await session.refresh(schedule)

    assert result.envelope.ok is True
    assert schedule.status == "active"
    grant = await session.scalar(select(AutomationGrant))
    assert grant is not None and grant.status == "active"


@pytest.mark.asyncio
async def test_r2_schedule_update_keeps_current_revision_active_until_confirmation(
    session, context
) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(
        session,
        context,
        "Todo dia às 7h30 me envie meu briefing.",
    )
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="create-then-update-schedule",
        idempotency_key="create-then-update-schedule-key",
        settings=settings,
    )
    await create_schedule(tool_context, CreateScheduleArguments(spec=daily_spec()))
    schedule = await session.scalar(select(ScheduledAutomation))
    assert schedule is not None
    original_name = schedule.name
    original_spec = dict(schedule.compiled_spec)
    original_next_run = schedule.next_run_at

    integration = await connected_calendar(session, context)
    updated_data = r2_schedule_spec(integration.id).model_dump(mode="json")
    updated_data["name"] = "Briefing diário aprimorado"
    updated_data["objective"] = "Crie o evento recorrente solicitado no calendário."
    updated_spec = ScheduleSpec.model_validate(updated_data)
    proposed = await update_schedule(
        tool_context,
        UpdateScheduleArguments(schedule_id=schedule.id, spec=updated_spec),
    )
    await session.flush()

    grants = list(
        (
            await session.scalars(
                select(AutomationGrant).order_by(AutomationGrant.automation_revision)
            )
        ).all()
    )
    assert proposed.code == "confirmation_required"
    assert "continuará ativa" in proposed.message
    assert "Confirme em uma nova mensagem até" in proposed.message
    assert schedule.status == "active"
    assert schedule.revision == 1
    assert schedule.name == original_name
    assert schedule.compiled_spec == original_spec
    assert schedule.next_run_at == original_next_run
    assert schedule.pending_revision == 2
    assert schedule.pending_spec is not None
    assert [grant.status for grant in grants] == ["active", "pending"]

    activated = await activate_pending_schedule(
        tool_context,
        schedule_id=schedule.id,
        revision=2,
        confirmation_message_id=inbound.id,
    )
    await session.flush()

    assert activated.code == "schedule_activated"
    assert schedule.status == "active"
    assert schedule.revision == 2
    assert schedule.name == "Briefing diário aprimorado"
    assert schedule.compiled_spec["objective"] == updated_spec.objective
    assert schedule.pending_revision is None
    assert schedule.pending_spec is None
    assert [grant.status for grant in grants] == ["revoked", "active"]


@pytest.mark.asyncio
async def test_r0_schedule_update_applies_without_second_confirmation(session, context) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(session, context, "Crie meu briefing diário.")
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="immediate-schedule-update",
        idempotency_key="immediate-schedule-update-key",
        settings=settings,
    )
    await create_schedule(tool_context, CreateScheduleArguments(spec=daily_spec()))
    schedule = await session.scalar(select(ScheduledAutomation))
    assert schedule is not None
    updated_data = daily_spec().model_dump(mode="json")
    updated_data["name"] = "Briefing sem confirmação redundante"

    result = await update_schedule(
        tool_context,
        UpdateScheduleArguments(
            schedule_id=schedule.id,
            spec=ScheduleSpec.model_validate(updated_data),
        ),
    )
    await session.flush()
    grants = list(
        (
            await session.scalars(
                select(AutomationGrant).order_by(AutomationGrant.automation_revision)
            )
        ).all()
    )

    assert result.code == "schedule_activated"
    assert schedule.status == "active"
    assert schedule.revision == 2
    assert schedule.name == "Briefing sem confirmação redundante"
    assert schedule.pending_revision is None
    assert await session.scalar(select(func.count(PendingAction.id))) == 0
    assert [grant.status for grant in grants] == ["revoked", "active"]


@pytest.mark.asyncio
async def test_cancelled_schedule_update_keeps_active_revision_unchanged(session, context) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(session, context, "Crie meu briefing diário.")
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="cancel-schedule-update",
        idempotency_key="cancel-schedule-update-key",
        settings=settings,
    )
    await create_schedule(tool_context, CreateScheduleArguments(spec=daily_spec()))
    schedule = await session.scalar(select(ScheduledAutomation))
    assert schedule is not None
    integration = await connected_calendar(session, context)
    updated_data = r2_schedule_spec(integration.id).model_dump(mode="json")
    updated_data["name"] = "Nome que não deve ser aplicado"
    await update_schedule(
        tool_context,
        UpdateScheduleArguments(
            schedule_id=schedule.id,
            spec=ScheduleSpec.model_validate(updated_data),
        ),
    )

    await cancel_pending_schedule(tool_context, schedule_id=schedule.id, revision=2)
    await session.flush()
    grants = list(
        (
            await session.scalars(
                select(AutomationGrant).order_by(AutomationGrant.automation_revision)
            )
        ).all()
    )

    assert schedule.status == "active"
    assert schedule.revision == 1
    assert schedule.name == "Briefing diário"
    assert schedule.pending_revision is None
    assert [grant.status for grant in grants] == ["active", "revoked"]


@pytest.mark.asyncio
async def test_expired_schedule_confirmation_can_be_renewed(session, context) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(session, context, "Crie meu briefing diário.")
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="renew-schedule-confirmation",
        idempotency_key="renew-schedule-confirmation-key",
        settings=settings,
    )
    integration = await connected_calendar(session, context)
    await create_schedule(
        tool_context,
        CreateScheduleArguments(spec=r2_schedule_spec(integration.id)),
    )
    expired = await session.scalar(select(PendingAction))
    assert expired is not None
    expired.status = "expired"
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    renewed = await renew_expired_schedule_confirmation(tool_context, expired)
    await session.flush()
    pending = list(
        (
            await session.scalars(
                select(PendingAction).order_by(PendingAction.created_at)
            )
        ).all()
    )

    assert renewed.code == "confirmation_renewed"
    assert "responda novamente até" in renewed.message
    assert [action.status for action in pending] == ["expired", "pending"]
    renewed_expiry = pending[-1].expires_at
    if renewed_expiry.tzinfo is None:
        renewed_expiry = renewed_expiry.replace(tzinfo=UTC)
    assert renewed_expiry > datetime.now(UTC) + timedelta(hours=23)


@pytest.mark.asyncio
async def test_late_one_time_confirmation_runs_within_grace(session, context) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(session, context, "Consulte e me avise depois.")
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="create-calendar-reminder",
        idempotency_key="create-calendar-reminder-key",
        settings=settings,
    )
    integration = await connected_calendar(session, context)
    result = await create_schedule(
        tool_context,
        CreateScheduleArguments(
            spec=r2_once_schedule_spec(
                integration.id,
                datetime.now(UTC) + timedelta(minutes=5),
            )
        ),
    )
    assert result.code == "confirmation_required"
    schedule = await session.scalar(select(ScheduledAutomation))
    assert schedule is not None
    late_start = datetime.now(UTC) - timedelta(seconds=30)
    data = schedule.compiled_spec.copy()
    data["trigger"] = {**data["trigger"], "starts_at": late_start.isoformat()}
    schedule.compiled_spec = data
    schedule.starts_at = late_start
    schedule.next_run_at = late_start
    await session.flush()

    activated = await activate_pending_schedule(
        tool_context,
        schedule_id=schedule.id,
        revision=schedule.revision,
        confirmation_message_id=inbound.id,
    )

    assert activated.code == "schedule_activated"
    assert schedule.status == "active"
    assert schedule.next_run_at is not None
    assert schedule.next_run_at >= late_start


@pytest.mark.asyncio
async def test_stale_unconfirmed_schedule_expires(session, context) -> None:
    settings = schedule_settings()
    conversation, inbound, run = await records(session, context, "Consulte e me avise depois.")
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="create-expiring-reminder",
        idempotency_key="create-expiring-reminder-key",
        settings=settings,
    )
    integration = await connected_calendar(session, context)
    await create_schedule(
        tool_context,
        CreateScheduleArguments(
            spec=r2_once_schedule_spec(
                integration.id,
                datetime.now(UTC) + timedelta(minutes=5),
            )
        ),
    )
    schedule = await session.scalar(select(ScheduledAutomation))
    assert schedule is not None
    schedule.next_run_at = datetime.now(UTC) - timedelta(minutes=2)
    pending = await session.scalar(select(PendingAction))
    assert pending is not None
    await session.commit()

    assert await expire_stale_schedules(session) is False
    await session.refresh(schedule)
    assert schedule.status == "awaiting_confirmation"

    pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    assert await expire_stale_schedules(session) is True
    await session.refresh(schedule)
    grant = await session.scalar(select(AutomationGrant))
    pending = await session.scalar(select(PendingAction))

    assert schedule.status == "expired"
    assert schedule.next_run_at is None
    assert grant is not None and grant.status == "revoked"
    assert pending is not None and pending.status == "expired"


@pytest.mark.asyncio
async def test_dispatcher_creates_one_run_and_materializes_orchestration(session, context) -> None:
    settings = schedule_settings()
    conversation, _, _ = await records(session, context, "setup")
    spec = daily_spec(datetime.now(UTC) - timedelta(days=1))
    schedule = ScheduledAutomation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        conversation_id=conversation.id,
        name=spec.name,
        original_request="briefing diário",
        compiled_spec=spec.model_dump(mode="json"),
        timezone=spec.trigger.timezone,
        recurrence_rule=spec.trigger.recurrence_rule,
        starts_at=spec.trigger.starts_at,
        next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        status="active",
        misfire_policy="latest",
        misfire_grace_seconds=21600,
        capabilities_snapshot=["memory_read", "integration_read", "schedule_execution"],
        tool_policy_snapshot=spec.tool_policy.model_dump(mode="json"),
    )
    session.add(schedule)
    await session.flush()
    session.add(
        AutomationGrant(
            workspace_id=context.workspace_id,
            user_id=context.identity.user_id,
            scheduled_automation_id=schedule.id,
            automation_revision=1,
            allowed_tools=spec.tool_policy.tools,
            allowed_account_ids=[],
            constraints={},
            max_risk="R0",
            status="active",
        )
    )
    await session.commit()

    assert await dispatch_due_schedule(session, settings) is True
    assert await dispatch_due_schedule(session, settings) is False
    assert await session.scalar(select(func.count(ScheduledRun.id))) == 1
    claimed = await claim_scheduled_run(session, "test-worker", settings)
    assert claimed is not None
    task = await materialize_scheduled_run(session, claimed, settings)

    assert isinstance(task, OrchestrationTask)
    assert task.routing_context["route"] == "scheduled"
    assert task.routing_context["schedule_spec"]["objective"] == spec.objective
    assert task.ack_outbox_id is None
