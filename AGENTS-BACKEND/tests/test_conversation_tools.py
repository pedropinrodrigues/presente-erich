from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings
from agents_backend.conversation.runtime import ConversationAgent
from agents_backend.conversation.tools import (
    ToolArguments,
    ToolEnvelope,
    ToolRegistry,
    ToolSpec,
)
from agents_backend.models import (
    AgentRun,
    ChannelMessage,
    Conversation,
    Fact,
    OrchestrationTask,
    PendingAction,
)
from agents_backend.schemas import ConversationRouteDecision


def conversation_settings(**overrides: str) -> Settings:
    values = {
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_ANON_KEY": "anon",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        "DATABASE_URL": "postgresql://postgres:password@db.project.supabase.co/postgres",
        "OPENAI_API_KEY": "openai-test",
        "OPENAI_MODEL_EXTRACTION": "gpt-5.6-luna",
        "OPENAI_MODEL_ANSWERING": "gpt-5.6-luna",
        "OPENAI_MODEL_CONVERSATION": "gpt-5.6-luna",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


async def conversation_records(
    session: AsyncSession,
    context: RequestContext,
    *,
    content: str,
) -> tuple[Conversation, ChannelMessage, AgentRun]:
    conversation = Conversation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="api",
        status="active",
        conversation_metadata={},
    )
    session.add(conversation)
    await session.flush()
    message = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="api",
        external_message_id=f"test:{uuid.uuid4()}",
        direction="inbound",
        content=content,
        status="processing",
        message_metadata={},
    )
    session.add(message)
    await session.flush()
    run = AgentRun(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        inbound_message_id=message.id,
        model="gpt-5.6-luna",
        prompt_version="test",
        status="running",
    )
    session.add(run)
    await session.commit()
    return conversation, message, run


def test_tool_schemas_are_strict_and_require_every_property() -> None:
    for definition in ToolRegistry().definitions():
        schema = definition["parameters"]
        assert definition["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


class EmptyArguments(ToolArguments):
    pass


@pytest.mark.asyncio
async def test_retryable_tool_failure_can_be_executed_again(
    session: AsyncSession, context: RequestContext
) -> None:
    conversation, message, run = await conversation_records(
        session, context, content="Consulte novamente"
    )
    attempts = 0

    async def transient_handler(*_: object) -> ToolEnvelope:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("synthetic")
        return ToolEnvelope(ok=True, code="ok", message="Concluído")

    registry = ToolRegistry(
        [ToolSpec("transient_test", "Teste transitório", EmptyArguments, "R0", transient_handler)]
    )
    kwargs = {
        "session": session,
        "request_context": context,
        "conversation": conversation,
        "inbound_message": message,
        "agent_run": run,
        "call_id": "same-call",
        "tool_name": "transient_test",
        "raw_arguments": "{}",
        "settings": conversation_settings(),
    }

    first = await registry.execute(**kwargs)  # type: ignore[arg-type]
    second = await registry.execute(**kwargs)  # type: ignore[arg-type]

    assert first.envelope.retryable is True
    assert second.envelope.ok is True
    assert second.replayed is False
    assert attempts == 2


@pytest.mark.asyncio
async def test_delete_requires_a_new_explicit_confirmation_turn(
    session: AsyncSession, context: RequestContext
) -> None:
    conversation, first_message, first_run = await conversation_records(
        session, context, content="Apague este fato"
    )
    fact = Fact(
        workspace_id=context.workspace_id,
        predicate="launch_date",
        value={"value": "20 de setembro"},
        value_text="20 de setembro",
        fact_type="decision",
        fingerprint="f" * 64,
        status="current",
        confidence=1.0,
    )
    session.add(fact)
    await session.commit()
    registry = ToolRegistry()
    raw_delete = json.dumps({"target_id": str(fact.id), "reason": "Pedido do usuário"})

    proposed = await registry.execute(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=first_message,
        agent_run=first_run,
        call_id="call-delete-1",
        tool_name="delete_memory",
        raw_arguments=raw_delete,
        settings=conversation_settings(),
    )
    replayed = await registry.execute(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=first_message,
        agent_run=first_run,
        call_id="call-delete-retry",
        tool_name="delete_memory",
        raw_arguments=raw_delete,
        settings=conversation_settings(),
    )
    same_turn = await registry.execute(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=first_message,
        agent_run=first_run,
        call_id="call-confirm-same-turn",
        tool_name="confirm_action",
        raw_arguments=json.dumps({"action_id": None}),
        settings=conversation_settings(),
    )

    await session.refresh(fact)
    assert proposed.envelope.code == "confirmation_required"
    assert replayed.replayed is True
    assert same_turn.envelope.code == "confirmation_requires_new_turn"
    assert fact.status == "current"
    assert await session.scalar(select(func.count(PendingAction.id))) == 1

    second_message = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="api",
        external_message_id=f"test:{uuid.uuid4()}",
        direction="inbound",
        content="Está certo, é exatamente isso que eu quero.",
        status="processing",
        message_metadata={},
    )
    session.add(second_message)
    await session.flush()
    second_run = AgentRun(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        inbound_message_id=second_message.id,
        model="gpt-5.6-luna",
        prompt_version="test",
        status="running",
    )
    session.add(second_run)
    confirmation_task = OrchestrationTask(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        conversation_id=conversation.id,
        inbound_message_id=second_message.id,
        intent="memory_deletion",
        request_text=second_message.content,
        summary="O usuário confirmou claramente a exclusão pendente.",
        routing_context={"confirmation_status": "explicit"},
        allowed_capabilities=["memory_read", "memory_deletion"],
        status="running",
        idempotency_key=f"test-confirm:{second_message.id}",
    )
    session.add(confirmation_task)
    await session.commit()
    confirmed = await registry.execute(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=second_message,
        agent_run=second_run,
        call_id="call-confirm-next-turn",
        tool_name="confirm_action",
        raw_arguments=json.dumps({"action_id": None}),
        settings=conversation_settings(),
        orchestration_task=confirmation_task,
    )

    await session.refresh(fact)
    action = await session.scalar(select(PendingAction))
    assert confirmed.envelope.code == "action_executed"
    assert fact.status == "deleted"
    assert action is not None and action.status == "executed"


class FakeFunctionCall:
    type = "function_call"

    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.call_id = call_id
        self.name = name
        self.arguments = arguments
        self.id = f"fc_{call_id}"

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
        }


class ScriptedGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = [
            SimpleNamespace(
                id="response-tools",
                output=[
                    FakeFunctionCall(
                        "call-list",
                        "list_open_commitments",
                        json.dumps(
                            {
                                "query": None,
                                "responsible_entity_id": None,
                                "limit": 10,
                            }
                        ),
                    )
                ],
                output_text="",
                usage=SimpleNamespace(input_tokens=10, output_tokens=4),
            ),
            SimpleNamespace(
                id="response-final",
                output=[],
                output_text="Você não tem compromissos abertos.",
                usage=SimpleNamespace(input_tokens=14, output_tokens=8),
            ),
        ]

    async def route_conversation(self, **_: Any) -> Any:
        return SimpleNamespace(
            value=ConversationRouteDecision(
                route="answer",
                understanding="O usuário quer consultar compromissos pendentes.",
                handoff_context="Consulta de leitura sobre compromissos em aberto.",
                confirmation_status="none",
                confidence=0.98,
            ),
            provider_request_id="routing-response",
            input_tokens=8,
            output_tokens=4,
        )

    async def conversation_response(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_runtime_returns_tool_output_with_matching_call_id(
    session: AsyncSession, context: RequestContext
) -> None:
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
        external_message_id="runtime-test",
        direction="inbound",
        content="Quais são minhas pendências?",
        status="processing",
        message_metadata={},
    )
    session.add(inbound)
    await session.commit()
    gateway = ScriptedGateway()
    agent = ConversationAgent(settings=conversation_settings(), gateway=gateway)

    result = await agent.run(session, context, conversation, inbound)
    replay = await agent.run(session, context, conversation, inbound)

    assert result.answer == "Você não tem compromissos abertos."
    assert replay.answer == result.answer
    assert replay.tools_used[0].idempotent_replay is True
    assert [tool.name for tool in result.tools_used] == ["list_open_commitments"]
    second_input = gateway.calls[1]["input_items"]
    tool_outputs = [item for item in second_input if item.get("type") == "function_call_output"]
    assert tool_outputs[0]["call_id"] == "call-list"
    assert json.loads(tool_outputs[0]["output"])["code"] == "open_commitments_found"
    assert gateway.calls[0]["safety_identifier"] != str(context.identity.user_id)
    assert len(gateway.calls) == 2
