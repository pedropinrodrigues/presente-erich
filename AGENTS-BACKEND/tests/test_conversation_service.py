from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_settings

from agents_backend.auth import RequestContext
from agents_backend.conversation.runtime import ConversationAgentResult
from agents_backend.conversation.service import ConversationService
from agents_backend.errors import ConflictError
from agents_backend.models import ChannelMessage, Conversation
from agents_backend.schemas import AgentTurnRequest


class FakeAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *args: object) -> ConversationAgentResult:
        self.calls += 1
        return ConversationAgentResult(
            answer="Resposta persistida",
            run_id=uuid.uuid4(),
            tools_used=[],
            pending_action=None,
        )


@pytest.mark.asyncio
async def test_api_turn_is_idempotent(session: AsyncSession, context: RequestContext) -> None:
    agent = FakeAgent()
    service = ConversationService(
        settings=conversation_settings(),
        agent=agent,  # type: ignore[arg-type]
    )
    request = AgentTurnRequest(message_id="client-1", message="Olá")

    first = await service.process_api_turn(session, context, request)
    replay = await service.process_api_turn(session, context, request)

    assert first.answer == replay.answer == "Resposta persistida"
    assert first.conversation_id == replay.conversation_id
    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert agent.calls == 1
    assert await session.scalar(select(func.count(Conversation.id))) == 1
    assert await session.scalar(select(func.count(ChannelMessage.id))) == 2


@pytest.mark.asyncio
async def test_api_message_id_cannot_be_reused_with_other_content(
    session: AsyncSession, context: RequestContext
) -> None:
    service = ConversationService(
        settings=conversation_settings(),
        agent=FakeAgent(),  # type: ignore[arg-type]
    )
    await service.process_api_turn(
        session, context, AgentTurnRequest(message_id="same-id", message="Primeira")
    )

    with pytest.raises(ConflictError) as error:
        await service.process_api_turn(
            session, context, AgentTurnRequest(message_id="same-id", message="Outra")
        )

    assert getattr(error.value, "code", None) == "message_id_conflict"


@pytest.mark.asyncio
async def test_help_command_bypasses_model(session: AsyncSession, context: RequestContext) -> None:
    agent = FakeAgent()
    service = ConversationService(
        settings=conversation_settings(),
        agent=agent,  # type: ignore[arg-type]
    )

    response = await service.process_api_turn(
        session,
        context,
        AgentTurnRequest(message_id="help-1", message="/ajuda"),
    )

    assert "Eu sou a Luna" in response.answer
    assert "não precisa decorar comandos" in response.answer
    assert "/macwhisper" in response.answer
    assert "somente para usuários autorizados" in response.answer
    assert agent.calls == 0
