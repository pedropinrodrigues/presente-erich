from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_settings

from agents_backend.auth import RequestContext
from agents_backend.memory.daily_conversations import dispatch_daily_conversation_memory
from agents_backend.models import ChannelMessage, Conversation, Job, Source


@pytest.mark.asyncio
async def test_daily_conversation_creates_one_idempotent_extraction_job(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = conversation_settings(
        APP_TIMEZONE="America/Sao_Paulo",
        DAILY_CONVERSATION_MEMORY_HOUR="3",
        DAILY_CONVERSATION_MEMORY_LOOKBACK_DAYS="1",
    )
    conversation = Conversation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="telegram",
        external_thread_id="daily-memory-chat",
        status="active",
        conversation_metadata={},
    )
    session.add(conversation)
    await session.flush()
    inbound = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="telegram",
        external_message_id="daily-memory:1",
        direction="inbound",
        content="Eu decidi priorizar a wiki e Ana revisará o conteúdo até sexta-feira.",
        status="completed",
        message_metadata={"input_type": "voice"},
        created_at=datetime(2026, 8, 25, 13, 0, tzinfo=UTC),
    )
    session.add(inbound)
    await session.flush()
    assistant = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        reply_to_message_id=inbound.id,
        provider="telegram",
        external_message_id="daily-memory:2",
        direction="outbound",
        content="Entendi. Vou guardar essa informação.",
        status="completed",
        message_metadata={"response_phase": "final"},
        created_at=datetime(2026, 8, 25, 13, 1, tzinfo=UTC),
    )
    scheduled = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="telegram",
        external_message_id=f"schedule:{uuid.uuid4()}",
        direction="inbound",
        content="Resumo automático que não foi escrito pelo usuário.",
        status="completed",
        message_metadata={"origin": "scheduled_automation"},
        created_at=datetime(2026, 8, 25, 14, 0, tzinfo=UTC),
    )
    canary = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="telegram",
        external_message_id=f"deploy-canary:{uuid.uuid4()}",
        direction="inbound",
        content="Canário de deploy.",
        status="completed",
        message_metadata={},
        created_at=datetime(2026, 8, 25, 15, 0, tzinfo=UTC),
    )
    session.add_all([assistant, scheduled, canary])
    await session.commit()

    now = datetime(2026, 8, 26, 6, 30, tzinfo=UTC)
    assert await dispatch_daily_conversation_memory(session, settings, now=now) is True
    assert await dispatch_daily_conversation_memory(session, settings, now=now) is False

    source = await session.scalar(select(Source).where(Source.source_type == "daily_conversation"))
    assert source is not None
    assert source.source_metadata["local_date"] == "2026-08-25"
    assert source.source_metadata["memory_policy"] == "user_authored_only"
    assert source.source_metadata["message_count"] == 2
    assert '"role":"user"' in str(source.transcript)
    assert '"role":"assistant"' in str(source.transcript)
    assert "priorizar a wiki" in str(source.transcript)
    assert "Resumo automático" not in str(source.transcript)
    assert "Canário de deploy" not in str(source.transcript)
    assert await session.scalar(select(func.count(Job.id))) == 1


@pytest.mark.asyncio
async def test_daily_conversation_waits_for_configured_local_hour(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = conversation_settings(
        APP_TIMEZONE="America/Sao_Paulo",
        DAILY_CONVERSATION_MEMORY_HOUR="3",
        DAILY_CONVERSATION_MEMORY_LOOKBACK_DAYS="1",
    )
    conversation = Conversation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="api",
        status="active",
        conversation_metadata={},
    )
    session.add(conversation)
    await session.flush()
    session.add(
        ChannelMessage(
            workspace_id=context.workspace_id,
            conversation_id=conversation.id,
            provider="api",
            external_message_id="before-hour",
            direction="inbound",
            content="Uma preferência importante.",
            status="completed",
            message_metadata={},
            created_at=datetime(2026, 8, 25, 13, 0, tzinfo=UTC),
        )
    )
    await session.commit()

    before_hour = datetime(2026, 8, 26, 5, 59, tzinfo=UTC)
    assert await dispatch_daily_conversation_memory(
        session, settings, now=before_hour
    ) is False
    assert await session.scalar(select(func.count(Source.id))) == 0
