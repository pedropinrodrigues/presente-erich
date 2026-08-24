from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from agents_backend.auth import RequestContext
from agents_backend.config import Settings
from agents_backend.models import (
    ChannelMessage,
    Conversation,
    OrchestrationTask,
    OrchestrationTaskEvent,
    PendingAction,
    WorkerHeartbeat,
)
from agents_backend.orchestration.service import reconcile_closed_pending_task
from agents_backend.worker.health import record_worker_heartbeat
from agents_backend.worker.main import _safe_error_message


def worker_settings() -> Settings:
    return Settings(
        _env_file=None,
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_ANON_KEY="anon",
        SUPABASE_SERVICE_ROLE_KEY="service-role",
        DATABASE_URL="postgresql://postgres:password@db.project.supabase.co/postgres",
        OPENAI_API_KEY="openai-test",
        OPENAI_MODEL_EXTRACTION="gpt-5.6-luna",
        OPENAI_MODEL_ANSWERING="gpt-5.6-luna",
        DEPLOYMENT_REVISION="test-revision",
    )  # type: ignore[arg-type]


async def inbound_records(session, context: RequestContext):
    conversation = Conversation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="telegram",
        status="active",
        conversation_metadata={},
    )
    session.add(conversation)
    await session.flush()
    inbound = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="telegram",
        external_message_id=f"health:{uuid.uuid4()}",
        direction="inbound",
        content="Teste de saúde do worker",
        status="received",
        message_metadata={},
        created_at=datetime.now(UTC) - timedelta(seconds=90),
    )
    session.add(inbound)
    await session.flush()
    return conversation, inbound


@pytest.mark.asyncio
async def test_worker_heartbeat_persists_queue_lag(session, context) -> None:
    await inbound_records(session, context)

    snapshot = await record_worker_heartbeat(
        session,
        worker_id="worker-test",
        status="healthy",
        consecutive_infra_failures=0,
        settings=worker_settings(),
    )
    heartbeat = await session.get(WorkerHeartbeat, "worker-test")

    assert heartbeat is not None
    assert heartbeat.status == "healthy"
    assert heartbeat.deployment_revision == "test-revision"
    assert snapshot["channel_inbound"]["count"] == 1
    assert snapshot["channel_inbound"]["oldest_lag_seconds"] >= 89
    assert heartbeat.heartbeat_metadata["max_lag_seconds"] >= 89


@pytest.mark.asyncio
async def test_closed_pending_action_reconciles_waiting_task(session, context) -> None:
    conversation, inbound = await inbound_records(session, context)
    task = OrchestrationTask(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        conversation_id=conversation.id,
        inbound_message_id=inbound.id,
        intent="automation",
        request_text=inbound.content,
        summary="Teste pendente",
        routing_context={},
        allowed_capabilities=["automation"],
        status="waiting_confirmation",
        idempotency_key=f"health:{uuid.uuid4()}",
    )
    session.add(task)
    await session.flush()
    action = PendingAction(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        user_id=context.identity.user_id,
        created_by_message_id=inbound.id,
        orchestration_task_id=task.id,
        tool_name="external_action",
        tool_version="1",
        arguments={},
        summary="Ação expirada",
        confirmation_token=f"health:{uuid.uuid4()}",
        status="pending",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    session.add(action)
    await session.commit()

    assert await reconcile_closed_pending_task(session) is True
    await session.refresh(task)
    await session.refresh(action)
    event = await session.scalar(
        select(OrchestrationTaskEvent).where(
            OrchestrationTaskEvent.orchestration_task_id == task.id
        )
    )

    assert action.status == "expired"
    assert task.status == "failed"
    assert task.error_code == "pending_action_expired"
    assert event is not None and event.event_type == "reconciled_failed"


def test_worker_error_message_redacts_credentials() -> None:
    error = RuntimeError(
        "postgresql://user:super-secret@database.test/db token=secret-value"
    )

    message = _safe_error_message(error)

    assert "super-secret" not in message
    assert "secret-value" not in message
    assert "[redacted]" in message
