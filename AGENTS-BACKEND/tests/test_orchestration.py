from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import (
    FakeFunctionCall,
    conversation_records,
    conversation_settings,
)

from agents_backend.auth import RequestContext
from agents_backend.conversation.channel_jobs import claim_outbox_message
from agents_backend.conversation.runtime import ConversationAgent
from agents_backend.conversation.tools import (
    ToolRegistry,
    delegation_tool_specs,
    orchestration_tool_specs,
)
from agents_backend.models import (
    ChannelAccount,
    ChannelMessage,
    Conversation,
    Job,
    OrchestrationTask,
    OrchestrationTaskEvent,
    OutboxMessage,
    PendingAction,
    Source,
)
from agents_backend.orchestration.policies import ACKNOWLEDGEMENT
from agents_backend.orchestration.runtime import OrchestrationAgent, OrchestrationResult
from agents_backend.orchestration.service import OrchestrationService
from agents_backend.schemas import AgentToolUseResponse, ConversationRouteDecision


def test_fast_agent_has_no_mutation_tools() -> None:
    agent = ConversationAgent(settings=conversation_settings())
    names = {definition["name"] for definition in agent.registry.definitions()}

    assert names == {
        "search_memory",
        "get_entity",
        "get_source_status",
        "list_open_commitments",
        "get_pending_action",
    }
    assert "remember_transcript" not in names
    assert "delete_memory" not in names


def test_orchestrator_catalog_is_restricted_by_capability() -> None:
    ingestion = {
        definition["name"]
        for definition in ToolRegistry(
            orchestration_tool_specs(["memory_read", "ingestion"])
        ).definitions()
    }
    unsupported = ToolRegistry(orchestration_tool_specs(["automation"])).definitions()
    web_research = {
        definition["name"]
        for definition in ToolRegistry(orchestration_tool_specs(["web_research"])).definitions()
    }

    assert "remember_transcript" in ingestion
    assert "search_memory" in ingestion
    assert "delete_memory" not in ingestion
    assert unsupported == []
    assert web_research == {"research_web"}


class StructuredRoutingGateway:
    def __init__(self, decision: ConversationRouteDecision) -> None:
        self.decision = decision
        self.routing_calls: list[dict[str, Any]] = []

    async def route_conversation(self, **kwargs: Any) -> Any:
        self.routing_calls.append(kwargs)
        return SimpleNamespace(
            value=self.decision,
            provider_request_id="route-1",
            input_tokens=12,
            output_tokens=8,
        )

    async def conversation_response(self, **_: Any) -> Any:
        raise AssertionError("A rota não deveria executar o agente de resposta")


@pytest.mark.asyncio
async def test_luna_structured_delegation_persists_context_before_acknowledgement(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    conversation, message, _ = await conversation_records(
        session,
        context,
        content="Apague aquela decisão da Ana.",
    )
    gateway = StructuredRoutingGateway(
        ConversationRouteDecision(
            route="delegate",
            understanding="O usuário quer excluir uma decisão atribuída à Ana.",
            handoff_context=(
                "A conversa menciona uma decisão relacionada à Ana, mas o alvo exato precisa "
                "ser localizado na memória antes de propor a exclusão. É o primeiro pedido, "
                "portanto ainda não existe confirmação posterior."
            ),
            orchestration_intent="memory_deletion",
            acknowledgement="Certo, vou cuidar disso e volto com uma resposta.",
            confirmation_status="none",
            confidence=0.93,
        )
    )
    agent = ConversationAgent(settings=conversation_settings(), gateway=gateway)

    result = await agent.run(session, context, conversation, message)

    task = await session.scalar(
        select(OrchestrationTask).where(OrchestrationTask.inbound_message_id == message.id)
    )
    assert result.answer == "Certo, vou cuidar disso e volto com uma resposta."
    assert task is not None
    assert task.routing_context["understanding"] == (
        "O usuário quer excluir uma decisão atribuída à Ana."
    )
    assert "primeiro pedido" in task.routing_context["handoff_context"]
    assert [tool.name for tool in result.tools_used] == ["delegate_to_orchestrator"]
    routing_context = gateway.routing_calls[0]["input_items"][0]["content"]
    assert '"user_profile"' in routing_context
    assert "Perfil contextual derivado da wiki" in routing_context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "message"),
    [
        ("clarify", "Qual decisão da Ana você quer alterar?"),
        ("request_confirmation", "Você confirma explicitamente a exclusão dessa decisão?"),
    ],
)
async def test_luna_can_ask_for_clarification_or_confirmation_without_acknowledgement(
    session: AsyncSession,
    context: RequestContext,
    route: str,
    message: str,
) -> None:
    conversation, inbound, _ = await conversation_records(
        session,
        context,
        content="A decisão da Ana deve ser apagada.",
    )
    if route == "request_confirmation":
        session.add(
            PendingAction(
                workspace_id=context.workspace_id,
                conversation_id=conversation.id,
                user_id=context.identity.user_id,
                created_by_message_id=inbound.id,
                tool_name="delete_memory",
                tool_version="1",
                arguments={"target_id": str(uuid.uuid4())},
                summary="Excluir a decisão atribuída à Ana",
                confirmation_token=f"test:{uuid.uuid4()}",
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()
    decision = ConversationRouteDecision(
        route=route,  # type: ignore[arg-type]
        understanding="O pedido ainda precisa de uma resposta do usuário.",
        handoff_context="Há informação insuficiente ou confirmação ambígua.",
        user_message=message,
        confirmation_status=("ambiguous" if route == "request_confirmation" else "none"),
        confidence=0.88,
    )
    gateway = StructuredRoutingGateway(decision)
    agent = ConversationAgent(settings=conversation_settings(), gateway=gateway)

    result = await agent.run(session, context, conversation, inbound)

    assert result.answer == message
    assert ACKNOWLEDGEMENT not in result.answer
    assert (
        await session.scalar(
            select(OrchestrationTask).where(OrchestrationTask.inbound_message_id == inbound.id)
        )
        is None
    )
    if route == "request_confirmation":
        routing_payload = gateway.routing_calls[0]["input_items"][0]["content"]
        assert "Excluir a decisão atribuída à Ana" in routing_payload


@pytest.mark.asyncio
async def test_delegation_persists_one_task_and_uses_backend_context(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    conversation, message, run = await conversation_records(
        session,
        context,
        content="Guarde esta transcrição: reunião com Ana.",
    )
    registry = ToolRegistry(delegation_tool_specs())
    arguments = json.dumps(
        {
            "intent": "memory_write",
            "summary": "Guardar a reunião com Ana.",
            "user_request": "texto adulterado que não deve ser autoridade",
            "handoff_context": "O usuário enviou uma transcrição de reunião com Ana.",
            "acknowledgement": "Vou analisar isso e retorno em seguida.",
            "confirmation_status": "none",
            "confidence": 0.97,
        }
    )
    kwargs = {
        "session": session,
        "request_context": context,
        "conversation": conversation,
        "inbound_message": message,
        "agent_run": run,
        "tool_name": "delegate_to_orchestrator",
        "raw_arguments": arguments,
        "settings": conversation_settings(),
    }

    first = await registry.execute(call_id="delegate-1", **kwargs)  # type: ignore[arg-type]
    second = await registry.execute(call_id="delegate-2", **kwargs)  # type: ignore[arg-type]

    task = await session.scalar(select(OrchestrationTask))
    assert first.envelope.message == ACKNOWLEDGEMENT
    assert second.envelope.message == ACKNOWLEDGEMENT
    assert task is not None
    assert task.request_text == message.content
    assert task.workspace_id == context.workspace_id
    assert task.user_id == context.identity.user_id
    assert task.allowed_capabilities == ["memory_read", "ingestion"]
    assert task.routing_context["handoff_context"] == (
        "O usuário enviou uma transcrição de reunião com Ana."
    )
    assert await session.scalar(select(func.count(OrchestrationTask.id))) == 1
    assert await session.scalar(select(func.count(OrchestrationTaskEvent.id))) == 1


class ScriptedOrchestrationGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = [
            SimpleNamespace(
                id="orchestration-tool",
                output=[
                    FakeFunctionCall(
                        "remember-1",
                        "remember_transcript",
                        json.dumps(
                            {
                                "transcript": "Ana confirmou a reunião.",
                                "captured_at": None,
                                "source": "telegram",
                                "language": "pt-BR",
                            }
                        ),
                    )
                ],
                output_text="",
                usage=SimpleNamespace(input_tokens=20, output_tokens=5),
            ),
            SimpleNamespace(
                id="orchestration-final",
                output=[],
                output_text="A transcrição foi recebida e será processada.",
                usage=SimpleNamespace(input_tokens=30, output_tokens=10),
            ),
        ]

    async def orchestration_response(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_orchestrator_executes_ingestion_without_exposing_other_tools(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    conversation, message, _ = await conversation_records(
        session,
        context,
        content="Guarde esta transcrição: Ana confirmou a reunião.",
    )
    task = OrchestrationTask(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        conversation_id=conversation.id,
        inbound_message_id=message.id,
        intent="memory_write",
        request_text=message.content,
        summary="Guardar transcrição.",
        allowed_capabilities=["memory_read", "ingestion"],
        status="running",
        idempotency_key=f"test:{message.id}",
    )
    session.add(task)
    await session.commit()
    gateway = ScriptedOrchestrationGateway()
    agent = OrchestrationAgent(settings=conversation_settings(), gateway=gateway)

    result = await agent.run(session, task)
    replay = await agent.run(session, task)

    assert result.answer == "A transcrição foi recebida e será processada."
    assert replay.answer == result.answer
    assert [item.name for item in result.tools_used] == ["remember_transcript"]
    assert replay.tools_used[0].idempotent_replay is True
    assert await session.scalar(select(func.count(Source.id))) == 1
    assert await session.scalar(select(func.count(Job.id))) == 1
    exposed = {definition["name"] for definition in gateway.calls[0]["tools"]}
    assert "remember_transcript" in exposed
    assert "delete_memory" not in exposed
    assert len(gateway.calls) == 2


class FakeOrchestrationAgent:
    async def run(
        self,
        session: AsyncSession,
        task: OrchestrationTask,
    ) -> OrchestrationResult:
        return OrchestrationResult(
            answer="Tarefa concluída.",
            run_id=uuid.uuid4(),
            tools_used=[
                AgentToolUseResponse(
                    name="remember_transcript",
                    status="completed",
                    risk_level="R1",
                )
            ],
            pending_action=None,
        )


@pytest.mark.asyncio
async def test_final_outbox_waits_for_acknowledgement(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    account = ChannelAccount(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        provider="telegram",
        external_account_id="123",
        active=True,
    )
    session.add(account)
    await session.flush()
    conversation = Conversation(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        channel_account_id=account.id,
        provider="telegram",
        external_thread_id="123",
        status="active",
        conversation_metadata={"chat_id": "123"},
    )
    session.add(conversation)
    await session.flush()
    inbound = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        provider="telegram",
        external_message_id="123:1",
        direction="inbound",
        content="Guarde isso.",
        status="completed",
        message_metadata={"sender": "123"},
    )
    session.add(inbound)
    await session.flush()
    ack_message = ChannelMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        reply_to_message_id=inbound.id,
        provider="telegram",
        direction="outbound",
        content=ACKNOWLEDGEMENT,
        status="queued",
        message_metadata={"response_phase": "acknowledgement"},
    )
    session.add(ack_message)
    await session.flush()
    ack = OutboxMessage(
        workspace_id=context.workspace_id,
        conversation_id=conversation.id,
        channel_message_id=ack_message.id,
        provider="telegram",
        destination="123",
        payload={"type": "text", "text": {"body": ACKNOWLEDGEMENT}},
        status="pending",
        idempotency_key="ack:test",
    )
    session.add(ack)
    await session.flush()
    task = OrchestrationTask(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        conversation_id=conversation.id,
        inbound_message_id=inbound.id,
        intent="memory_write",
        request_text=inbound.content,
        summary="Guardar conteúdo.",
        allowed_capabilities=["memory_read", "ingestion"],
        status="running",
        idempotency_key="task:test",
        ack_outbox_id=ack.id,
    )
    session.add(task)
    await session.commit()

    service = OrchestrationService(FakeOrchestrationAgent())  # type: ignore[arg-type]
    await service.process(session, task)
    result_outbox = await session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.idempotency_key == f"orchestration-result:{task.id}"
        )
    )
    assert result_outbox is not None
    assert result_outbox.depends_on_outbox_id == ack.id

    claimed = await claim_outbox_message(session, "worker")
    assert claimed is not None and claimed.id == ack.id
    claimed.status = "sent"
    await session.commit()

    final = await claim_outbox_message(session, "worker")
    assert final is not None and final.id == result_outbox.id
