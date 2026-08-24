from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings, get_settings
from agents_backend.errors import AppError, NotFoundError
from agents_backend.ingestion.service import ingest_transcript
from agents_backend.memory.mutations import correct_memory, delete_memory_target, delete_source
from agents_backend.models import (
    AgentRun,
    ChannelMessage,
    Commitment,
    Conversation,
    Evidence,
    Fact,
    OrchestrationIntent,
    OrchestrationTask,
    OrchestrationTaskEvent,
    OrchestrationTaskStatus,
    PendingAction,
    Source,
    ToolExecution,
)
from agents_backend.orchestration.policies import (
    ACKNOWLEDGEMENT,
    capabilities_for_intent,
    tool_names_for_capabilities,
)
from agents_backend.retrieval.service import get_entity_view, search_memory
from agents_backend.schemas import CorrectionRequest, TranscriptEvent

TOOL_VERSION = "1"
logger = logging.getLogger(__name__)


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchMemoryArguments(ToolArguments):
    query: str | None
    entity_id: uuid.UUID | None
    item_type: Literal["entity", "fact", "commitment"] | None
    status: str | None
    from_at: datetime | None
    to_at: datetime | None
    limit: int = Field(ge=1, le=20)


class GetEntityArguments(ToolArguments):
    entity_id: uuid.UUID


class GetSourceStatusArguments(ToolArguments):
    source_id: uuid.UUID


class ListOpenCommitmentsArguments(ToolArguments):
    query: str | None
    responsible_entity_id: uuid.UUID | None
    limit: int = Field(ge=1, le=20)


class GetPendingActionArguments(ToolArguments):
    action_id: uuid.UUID | None


class RememberTranscriptArguments(ToolArguments):
    transcript: str = Field(min_length=1, max_length=500_000)
    captured_at: datetime | None
    source: str | None = Field(min_length=1, max_length=50)
    language: str | None = Field(min_length=2, max_length=20)


class CorrectMemoryArguments(ToolArguments):
    target_id: uuid.UUID
    target_type: Literal["fact", "commitment"]
    value: str
    reason: str | None


class DisputeMemoryArguments(ToolArguments):
    target_id: uuid.UUID
    target_type: Literal["fact", "commitment"]
    reason: str | None


class DeleteMemoryArguments(ToolArguments):
    target_id: uuid.UUID
    reason: str | None


class DeleteSourceArguments(ToolArguments):
    source_id: uuid.UUID
    reason: str | None


class ConfirmActionArguments(ToolArguments):
    action_id: uuid.UUID | None


class CancelActionArguments(ToolArguments):
    action_id: uuid.UUID | None


class DelegateToOrchestratorArguments(ToolArguments):
    intent: OrchestrationIntent
    summary: str = Field(min_length=1, max_length=1000)
    user_request: str = Field(min_length=1, max_length=20_000)
    handoff_context: str = Field(min_length=1, max_length=5000)
    acknowledgement: str = Field(min_length=1, max_length=240)
    confirmation_status: Literal["none", "explicit", "ambiguous", "cancellation"]
    confidence: float = Field(ge=0, le=1)


class ToolEnvelope(BaseModel):
    ok: bool
    code: str
    message: str
    data: dict[str, Any] | list[Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    retryable: bool = False


@dataclass(slots=True)
class ToolContext:
    session: AsyncSession
    request_context: RequestContext
    conversation: Conversation
    inbound_message: ChannelMessage
    agent_run: AgentRun
    call_id: str
    idempotency_key: str
    settings: Settings
    orchestration_task: OrchestrationTask | None = None


ToolHandler = Callable[[ToolContext, Any], Awaitable[ToolEnvelope]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    arguments_model: type[ToolArguments]
    risk_level: Literal["R0", "R1", "R2"]
    handler: ToolHandler
    version: str = TOOL_VERSION

    def openai_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": _strict_tool_schema(self.arguments_model.model_json_schema()),
            "strict": True,
        }


def _strict_tool_schema(value: Any) -> Any:
    """Normalize Pydantic defaults for strict Responses API function schemas."""
    if isinstance(value, list):
        return [_strict_tool_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {key: _strict_tool_schema(item) for key, item in value.items() if key != "default"}
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["required"] = list(properties)
        normalized["additionalProperties"] = False
    return normalized


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    envelope: ToolEnvelope
    execution_id: uuid.UUID
    name: str
    risk_level: str
    replayed: bool


def _jsonable(value: BaseModel | dict[str, Any] | list[Any]) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _success(
    code: str,
    message: str,
    data: dict[str, Any] | list[Any] | None = None,
    *,
    evidence: list[dict[str, Any]] | None = None,
) -> ToolEnvelope:
    return ToolEnvelope(
        ok=True,
        code=code,
        message=message,
        data=data,
        evidence=evidence or [],
    )


def _failure(code: str, message: str, *, retryable: bool = False) -> ToolEnvelope:
    return ToolEnvelope(ok=False, code=code, message=message, retryable=retryable)


async def _search_memory(context: ToolContext, arguments: SearchMemoryArguments) -> ToolEnvelope:
    result = await search_memory(
        context.session,
        context.request_context,
        query=arguments.query,
        entity_id=arguments.entity_id,
        item_type=arguments.item_type,
        status=arguments.status,
        from_=arguments.from_at,
        to=arguments.to_at,
        cursor=None,
        limit=arguments.limit,
    )
    data = result.model_dump(mode="json")
    evidence = [evidence for item in data["items"] for evidence in item.get("evidence", [])]
    return _success(
        "memory_found", f"Encontrei {len(result.items)} item(ns).", data, evidence=evidence
    )


async def _get_entity(context: ToolContext, arguments: GetEntityArguments) -> ToolEnvelope:
    result = await get_entity_view(context.session, context.request_context, arguments.entity_id)
    data = result.model_dump(mode="json")
    evidence = [
        evidence
        for collection in (data["facts"], data["commitments"], data["history"])
        for item in collection
        for evidence in item.get("evidence", [])
    ]
    return _success("entity_found", "Entidade localizada.", data, evidence=evidence)


async def _get_source_status(
    context: ToolContext, arguments: GetSourceStatusArguments
) -> ToolEnvelope:
    source = await context.session.scalar(
        select(Source).where(
            Source.id == arguments.source_id,
            Source.workspace_id == context.request_context.workspace_id,
            Source.status != "deleted",
        )
    )
    if source is None:
        raise NotFoundError()
    data = {
        "id": str(source.id),
        "capture_id": str(source.capture_id),
        "source": source.source_type,
        "captured_at": source.captured_at.isoformat(),
        "status": source.status,
        "error_code": source.error_code,
        "created_at": source.created_at.isoformat(),
    }
    return _success("source_found", f"A fonte está com status {source.status}.", data)


async def _list_open_commitments(
    context: ToolContext, arguments: ListOpenCommitmentsArguments
) -> ToolEnvelope:
    result = await search_memory(
        context.session,
        context.request_context,
        query=arguments.query,
        entity_id=arguments.responsible_entity_id,
        item_type="commitment",
        status="open",
        from_=None,
        to=None,
        cursor=None,
        limit=arguments.limit,
    )
    data = result.model_dump(mode="json")
    evidence = [evidence for item in data["items"] for evidence in item.get("evidence", [])]
    return _success(
        "open_commitments_found",
        f"Encontrei {len(result.items)} compromisso(s) aberto(s).",
        data,
        evidence=evidence,
    )


async def _active_pending_actions(
    context: ToolContext, action_id: uuid.UUID | None, *, lock: bool = False
) -> list[PendingAction]:
    now = datetime.now(UTC)
    expired = list(
        (
            await context.session.scalars(
                select(PendingAction).where(
                    PendingAction.workspace_id == context.request_context.workspace_id,
                    PendingAction.conversation_id == context.conversation.id,
                    PendingAction.user_id == context.request_context.identity.user_id,
                    PendingAction.status == "pending",
                    PendingAction.expires_at <= now,
                )
            )
        ).all()
    )
    for action in expired:
        action.status = "expired"
    statement = select(PendingAction).where(
        PendingAction.workspace_id == context.request_context.workspace_id,
        PendingAction.conversation_id == context.conversation.id,
        PendingAction.user_id == context.request_context.identity.user_id,
        PendingAction.status == "pending",
        PendingAction.expires_at > now,
    )
    if action_id is not None:
        statement = statement.where(PendingAction.id == action_id)
    if lock:
        statement = statement.with_for_update()
    return list((await context.session.scalars(statement.order_by(PendingAction.created_at))).all())


def _pending_data(action: PendingAction) -> dict[str, Any]:
    return {
        "id": str(action.id),
        "summary": action.summary,
        "status": action.status,
        "expires_at": action.expires_at.isoformat(),
    }


def _normalize_message(message: str) -> str:
    normalized = unicodedata.normalize("NFKD", message.casefold())
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-z0-9\s-]", " ", normalized)
    return " ".join(normalized.split())


def _has_explicit_intent(message: str, verbs: set[str]) -> bool:
    normalized = _normalize_message(message)
    alternatives = "|".join(re.escape(verb) for verb in sorted(verbs))
    if re.search(
        rf"\b(?:nao|nunca)\b(?:\s+\w+){{0,3}}\s+(?:{alternatives})\b",
        normalized,
    ):
        return False
    return re.search(rf"\b(?:{alternatives})\b", normalized) is not None


def _is_explicit_confirmation(message: str) -> bool:
    normalized = _normalize_message(message)
    if re.search(r"\b(?:nao|nunca|cancele|cancelar|cancelo)\b", normalized):
        return False
    if normalized in {
        "sim",
        "confirmo",
        "confirmado",
        "sim confirmo",
        "pode confirmar",
        "pode apagar",
        "pode excluir",
        "confirmo a exclusao",
        "confirmo essa exclusao",
        "confirmo esta exclusao",
    }:
        return True
    if re.search(r"\b(?:confirmo|confirmado|autorizo)\b", normalized):
        return True
    return bool(
        re.search(r"\b(?:sim|pode|quero|deve)\b", normalized)
        and re.search(r"\b(?:apagar|apague|excluir|exclua|remover|remova|prosseguir)\b", normalized)
    )


def _routing_confirmation_status(context: ToolContext) -> str:
    if context.orchestration_task is None:
        return "none"
    return str(context.orchestration_task.routing_context.get("confirmation_status") or "none")


async def _get_pending_action(
    context: ToolContext, arguments: GetPendingActionArguments
) -> ToolEnvelope:
    actions = await _active_pending_actions(context, arguments.action_id)
    if not actions:
        return _failure("no_pending_action", "Não há ação pendente válida nesta conversa.")
    return _success(
        "pending_action_found",
        f"Há {len(actions)} ação(ões) aguardando confirmação.",
        [_pending_data(action) for action in actions],
    )


async def _delegate_to_orchestrator(
    context: ToolContext, arguments: DelegateToOrchestratorArguments
) -> ToolEnvelope:
    existing = await context.session.scalar(
        select(OrchestrationTask).where(
            OrchestrationTask.workspace_id == context.request_context.workspace_id,
            OrchestrationTask.inbound_message_id == context.inbound_message.id,
        )
    )
    if existing is not None:
        return _success(
            "orchestration_task_queued",
            ACKNOWLEDGEMENT,
            {"task_id": str(existing.id), "status": existing.status},
        )

    task = OrchestrationTask(
        workspace_id=context.request_context.workspace_id,
        user_id=context.request_context.identity.user_id,
        conversation_id=context.conversation.id,
        inbound_message_id=context.inbound_message.id,
        intent=arguments.intent.value,
        request_text=context.inbound_message.content,
        summary=arguments.summary,
        routing_context={
            "route": "delegate",
            "understanding": arguments.summary,
            "handoff_context": arguments.handoff_context,
            "acknowledgement": arguments.acknowledgement,
            "confirmation_status": arguments.confirmation_status,
            "confidence": arguments.confidence,
        },
        allowed_capabilities=capabilities_for_intent(arguments.intent),
        status=OrchestrationTaskStatus.QUEUED.value,
        idempotency_key=f"route:{context.inbound_message.id}:v1",
        max_attempts=context.settings.orchestration_task_max_attempts,
    )
    context.session.add(task)
    await context.session.flush()
    context.session.add(
        OrchestrationTaskEvent(
            workspace_id=task.workspace_id,
            orchestration_task_id=task.id,
            event_type="created",
            event_metadata={"intent": task.intent},
        )
    )
    return _success(
        "orchestration_task_queued",
        ACKNOWLEDGEMENT,
        {"task_id": str(task.id), "status": task.status},
    )


async def _remember_transcript(
    context: ToolContext, arguments: RememberTranscriptArguments
) -> ToolEnvelope:
    if not _has_explicit_intent(
        context.inbound_message.content,
        {"guarde", "guardar", "memorize", "memorizar", "registre", "registrar", "salve", "salvar"},
    ):
        return _failure(
            "explicit_write_intent_required",
            "A mensagem atual não pede explicitamente para guardar esta transcrição.",
        )
    capture_key = (
        f"orchestration:{context.orchestration_task.id}:remember_transcript"
        if context.orchestration_task is not None
        else context.idempotency_key
    )
    capture_id = uuid.uuid5(uuid.NAMESPACE_URL, capture_key)
    event = TranscriptEvent(
        capture_id=capture_id,
        source=(arguments.source or context.conversation.provider).strip(),
        captured_at=arguments.captured_at or datetime.now(UTC),
        transcript=arguments.transcript,
        language=arguments.language or "pt-BR",
        metadata={
            "conversation_id": str(context.conversation.id),
            "message_id": str(context.inbound_message.id),
        },
    )
    result, _ = await ingest_transcript(
        context.session, context.request_context, event, commit=False
    )
    return _success(
        "transcript_accepted",
        "A transcrição foi aceita e será processada.",
        result.model_dump(mode="json"),
    )


async def _correct_memory(context: ToolContext, arguments: CorrectMemoryArguments) -> ToolEnvelope:
    if not _has_explicit_intent(
        context.inbound_message.content,
        {"altere", "alterar", "atualize", "atualizar", "corrija", "corrigir", "mude", "mudar"},
    ) and "esta errado" not in _normalize_message(context.inbound_message.content):
        return _failure(
            "explicit_write_intent_required",
            "A mensagem atual não pede explicitamente uma correção.",
        )
    result = await correct_memory(
        context.session,
        context.request_context,
        CorrectionRequest(
            target_id=arguments.target_id,
            target_type=arguments.target_type,
            operation="replace",
            value=arguments.value,
            reason=arguments.reason,
        ),
        commit=False,
    )
    return _success("memory_corrected", "A memória foi corrigida.", result.model_dump(mode="json"))


async def _dispute_memory(context: ToolContext, arguments: DisputeMemoryArguments) -> ToolEnvelope:
    normalized = _normalize_message(context.inbound_message.content)
    if not _has_explicit_intent(
        context.inbound_message.content,
        {"conteste", "contestar", "contesto", "discordo"},
    ) and not any(phrase in normalized for phrase in ("nao e verdade", "esta incorreto")):
        return _failure(
            "explicit_write_intent_required",
            "A mensagem atual não pede explicitamente uma contestação.",
        )
    result = await correct_memory(
        context.session,
        context.request_context,
        CorrectionRequest(
            target_id=arguments.target_id,
            target_type=arguments.target_type,
            operation="dispute",
            reason=arguments.reason,
        ),
        commit=False,
    )
    return _success(
        "memory_disputed", "A memória foi marcada como contestada.", result.model_dump(mode="json")
    )


async def create_pending_action(
    context: ToolContext,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    summary: str,
) -> PendingAction:
    now = datetime.now(UTC)
    signature = hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing = await context.session.scalar(
        select(PendingAction).where(
            PendingAction.workspace_id == context.request_context.workspace_id,
            PendingAction.conversation_id == context.conversation.id,
            PendingAction.user_id == context.request_context.identity.user_id,
            PendingAction.tool_name == tool_name,
            PendingAction.status == "pending",
            PendingAction.expires_at > now,
            PendingAction.confirmation_token.like(f"{signature[:16]}:%"),
        )
    )
    if existing is not None:
        return existing
    action = PendingAction(
        workspace_id=context.request_context.workspace_id,
        conversation_id=context.conversation.id,
        user_id=context.request_context.identity.user_id,
        created_by_message_id=context.inbound_message.id,
        orchestration_task_id=(
            context.orchestration_task.id if context.orchestration_task is not None else None
        ),
        tool_name=tool_name,
        tool_version=TOOL_VERSION,
        arguments=arguments,
        summary=summary,
        confirmation_token=f"{signature[:16]}:{secrets.token_urlsafe(24)}",
        status="pending",
        expires_at=now + timedelta(seconds=context.settings.pending_action_ttl_seconds),
    )
    context.session.add(action)
    await context.session.flush()
    return action


async def _delete_memory(context: ToolContext, arguments: DeleteMemoryArguments) -> ToolEnvelope:
    if not _has_explicit_intent(
        context.inbound_message.content,
        {"apague", "apagar", "delete", "deletar", "exclua", "excluir", "remova", "remover"},
    ):
        return _failure(
            "explicit_delete_intent_required",
            "A mensagem atual não pede explicitamente uma exclusão.",
        )
    fact = await context.session.scalar(
        select(Fact).where(
            Fact.id == arguments.target_id,
            Fact.workspace_id == context.request_context.workspace_id,
            Fact.status != "deleted",
        )
    )
    target: Fact | Commitment | None = fact
    target_type = "fact"
    label = fact.value_text if fact is not None else ""
    if target is None:
        commitment = await context.session.scalar(
            select(Commitment).where(
                Commitment.id == arguments.target_id,
                Commitment.workspace_id == context.request_context.workspace_id,
                Commitment.status != "deleted",
            )
        )
        target = commitment
        target_type = "commitment"
        label = commitment.description if commitment is not None else ""
    if target is None:
        raise NotFoundError()
    action = await create_pending_action(
        context,
        tool_name="delete_memory",
        arguments={
            "target_id": str(arguments.target_id),
            "target_type": target_type,
            "reason": arguments.reason,
        },
        summary=f"Excluir {target_type} permanentemente: {label[:700]}",
    )
    return _success(
        "confirmation_required",
        "A exclusão não foi executada. É necessária uma confirmação em outra mensagem.",
        _pending_data(action),
    )


async def _delete_source(context: ToolContext, arguments: DeleteSourceArguments) -> ToolEnvelope:
    if not _has_explicit_intent(
        context.inbound_message.content,
        {"apague", "apagar", "delete", "deletar", "exclua", "excluir", "remova", "remover"},
    ):
        return _failure(
            "explicit_delete_intent_required",
            "A mensagem atual não pede explicitamente uma exclusão.",
        )
    source = await context.session.scalar(
        select(Source).where(
            Source.id == arguments.source_id,
            Source.workspace_id == context.request_context.workspace_id,
            Source.status != "deleted",
        )
    )
    if source is None:
        raise NotFoundError()
    linked_count = await context.session.scalar(
        select(func.count(Evidence.id)).where(
            Evidence.workspace_id == context.request_context.workspace_id,
            Evidence.source_id == source.id,
        )
    )
    action = await create_pending_action(
        context,
        tool_name="delete_source",
        arguments={"source_id": str(arguments.source_id), "reason": arguments.reason},
        summary=(
            f"Excluir a fonte {source.id} de {source.captured_at.isoformat()} e remover "
            f"{linked_count or 0} evidência(s) vinculada(s)"
        ),
    )
    return _success(
        "confirmation_required",
        "A fonte não foi excluída. É necessária uma confirmação em outra mensagem.",
        _pending_data(action),
    )


async def _confirm_action(context: ToolContext, arguments: ConfirmActionArguments) -> ToolEnvelope:
    actions = await _active_pending_actions(context, arguments.action_id, lock=True)
    if not actions:
        return _failure("no_pending_action", "Não há ação pendente válida para confirmar.")
    if len(actions) > 1:
        return _failure(
            "ambiguous_pending_action",
            "Há mais de uma ação pendente; informe qual ação deve ser confirmada.",
        )
    action = actions[0]
    if action.created_by_message_id == context.inbound_message.id:
        return _failure(
            "confirmation_requires_new_turn",
            "A confirmação precisa ser enviada em uma nova mensagem do usuário.",
        )
    if _routing_confirmation_status(context) != "explicit" and not _is_explicit_confirmation(
        context.inbound_message.content
    ):
        return _failure(
            "explicit_confirmation_required",
            "A mensagem atual não é uma confirmação explícita da ação pendente.",
        )
    now = datetime.now(UTC)
    action.status = "executing"
    action.confirmed_at = now
    await context.session.flush()
    if action.tool_name == "delete_memory":
        result = await delete_memory_target(
            context.session,
            context.request_context,
            uuid.UUID(str(action.arguments["target_id"])),
            action.arguments.get("reason"),
            commit=False,
        )
    elif action.tool_name == "delete_source":
        result = await delete_source(
            context.session,
            context.request_context,
            uuid.UUID(str(action.arguments["source_id"])),
            action.arguments.get("reason"),
            commit=False,
        )
    elif action.tool_name == "external_action":
        from agents_backend.integrations.composio.service import execute_pending_external_action

        external = await execute_pending_external_action(context, action)
        if not external.ok:
            action.status = "failed"
            return external
        result = external
    elif action.tool_name == "activate_schedule":
        from agents_backend.scheduling.service import activate_pending_schedule

        scheduled = await activate_pending_schedule(
            context,
            schedule_id=uuid.UUID(str(action.arguments["schedule_id"])),
            revision=int(action.arguments["revision"]),
            confirmation_message_id=context.inbound_message.id,
        )
        if not scheduled.ok:
            action.status = "failed"
            return scheduled
        result = scheduled
    else:
        raise AppError("unsupported_pending_action", "A ação pendente não é suportada.", 409)
    action.status = "executed"
    action.executed_at = datetime.now(UTC)
    if action.orchestration_task_id is not None:
        original_task = await context.session.get(OrchestrationTask, action.orchestration_task_id)
        if original_task is not None:
            original_task.status = OrchestrationTaskStatus.COMPLETED.value
            original_task.result_code = "confirmed_action_executed"
            original_task.completed_at = datetime.now(UTC)
    return _success(
        "action_executed",
        "A ação confirmada foi executada.",
        {
            "action": _pending_data(action),
            "result": result.model_dump(mode="json"),
        },
    )


async def _cancel_action(context: ToolContext, arguments: CancelActionArguments) -> ToolEnvelope:
    if _routing_confirmation_status(context) != "cancellation" and _normalize_message(
        context.inbound_message.content
    ) not in {
        "cancele",
        "cancelar",
        "cancelo",
        "nao",
        "nao quero",
        "pode cancelar",
    }:
        return _failure(
            "explicit_cancellation_required",
            "A mensagem atual não pede explicitamente o cancelamento.",
        )
    actions = await _active_pending_actions(context, arguments.action_id, lock=True)
    if not actions:
        return _failure("no_pending_action", "Não há ação pendente válida para cancelar.")
    if len(actions) > 1:
        return _failure(
            "ambiguous_pending_action",
            "Há mais de uma ação pendente; informe qual ação deve ser cancelada.",
        )
    action = actions[0]
    action.status = "cancelled"
    action.cancelled_at = datetime.now(UTC)
    if action.tool_name == "external_action":
        from agents_backend.models import ExternalAction

        external_id = action.arguments.get("external_action_id")
        if external_id:
            external = await context.session.get(ExternalAction, uuid.UUID(str(external_id)))
            if external is not None:
                external.status = "cancelled"
                external.completed_at = datetime.now(UTC)
    elif action.tool_name == "activate_schedule":
        from agents_backend.scheduling.service import cancel_pending_schedule

        await cancel_pending_schedule(
            context,
            schedule_id=uuid.UUID(str(action.arguments["schedule_id"])),
            revision=int(action.arguments["revision"]),
        )
    if action.orchestration_task_id is not None:
        original_task = await context.session.get(OrchestrationTask, action.orchestration_task_id)
        if original_task is not None:
            original_task.status = OrchestrationTaskStatus.CANCELLED.value
            original_task.result_code = "pending_action_cancelled"
            original_task.completed_at = datetime.now(UTC)
    return _success("action_cancelled", "A ação pendente foi cancelada.", _pending_data(action))


def default_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "search_memory",
            "Busca entidades, fatos ou compromissos na memória do usuário.",
            SearchMemoryArguments,
            "R0",
            _search_memory,
        ),
        ToolSpec(
            "get_entity",
            "Obtém detalhes e histórico de uma entidade por ID retornado pela busca.",
            GetEntityArguments,
            "R0",
            _get_entity,
        ),
        ToolSpec(
            "get_source_status",
            "Consulta o estado de processamento de uma fonte por ID.",
            GetSourceStatusArguments,
            "R0",
            _get_source_status,
        ),
        ToolSpec(
            "list_open_commitments",
            "Lista compromissos ainda abertos do usuário.",
            ListOpenCommitmentsArguments,
            "R0",
            _list_open_commitments,
        ),
        ToolSpec(
            "get_pending_action",
            "Consulta ações destrutivas que aguardam confirmação nesta conversa.",
            GetPendingActionArguments,
            "R0",
            _get_pending_action,
        ),
        ToolSpec(
            "remember_transcript",
            "Guarda uma transcrição quando o usuário pede explicitamente para memorizá-la.",
            RememberTranscriptArguments,
            "R1",
            _remember_transcript,
        ),
        ToolSpec(
            "correct_memory",
            "Substitui um fato ou compromisso inequívoco por um valor corrigido.",
            CorrectMemoryArguments,
            "R1",
            _correct_memory,
        ),
        ToolSpec(
            "dispute_memory",
            "Marca um fato ou compromisso inequívoco como contestado.",
            DisputeMemoryArguments,
            "R1",
            _dispute_memory,
        ),
        ToolSpec(
            "delete_memory",
            "Propõe excluir um fato ou compromisso; nunca exclui no primeiro turno.",
            DeleteMemoryArguments,
            "R2",
            _delete_memory,
        ),
        ToolSpec(
            "delete_source",
            "Propõe excluir uma fonte e suas evidências; nunca exclui no primeiro turno.",
            DeleteSourceArguments,
            "R2",
            _delete_source,
        ),
        ToolSpec(
            "confirm_action",
            "Confirma uma ação pendente após confirmação explícita do usuário em outro turno.",
            ConfirmActionArguments,
            "R2",
            _confirm_action,
        ),
        ToolSpec(
            "cancel_action",
            "Cancela uma ação destrutiva pendente.",
            CancelActionArguments,
            "R1",
            _cancel_action,
        ),
        ToolSpec(
            "delegate_to_orchestrator",
            (
                "Delega uma alteração, automação, comunicação ou administração para execução "
                "assíncrona. Não realiza a ação neste turno."
            ),
            DelegateToOrchestratorArguments,
            "R0",
            _delegate_to_orchestrator,
        ),
    ]


FAST_TOOL_NAMES = {
    "search_memory",
    "get_entity",
    "get_source_status",
    "list_open_commitments",
    "get_pending_action",
}


def fast_tool_specs() -> list[ToolSpec]:
    return [spec for spec in default_tool_specs() if spec.name in FAST_TOOL_NAMES]


def delegation_tool_specs() -> list[ToolSpec]:
    return [spec for spec in default_tool_specs() if spec.name == "delegate_to_orchestrator"]


def orchestration_tool_specs(capabilities: list[str]) -> list[ToolSpec]:
    allowed = tool_names_for_capabilities(capabilities)
    specs = [spec for spec in default_tool_specs() if spec.name in allowed]
    if "schedule_management" in capabilities:
        from agents_backend.scheduling.service import schedule_tool_specs

        specs.extend(schedule_tool_specs())
    return specs


def _normalized_json(raw_arguments: str) -> tuple[dict[str, Any], str]:
    parsed = json.loads(raw_arguments)
    if not isinstance(parsed, dict):
        raise ValueError("argumentos devem formar um objeto JSON")
    encoded = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return parsed, hashlib.sha256(encoded.encode()).hexdigest()


def _sanitized_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(arguments)
    for field in (
        "transcript",
        "user_request",
        "handoff_context",
        "body",
        "text",
        "description",
        "recipient_email",
        "to_number",
        "attendees",
    ):
        if field not in sanitized:
            continue
        value = str(sanitized[field])
        sanitized[field] = {
            "redacted": True,
            "length": len(value),
            "sha256": hashlib.sha256(value.encode()).hexdigest(),
        }
    return sanitized


def _sanitized_result(envelope: ToolEnvelope) -> dict[str, Any]:
    value = envelope.model_dump(mode="json")
    data = value.get("data")
    if isinstance(data, dict) and "authorization_url" in data:
        url = str(data["authorization_url"])
        data["authorization_url"] = {
            "redacted": True,
            "sha256": hashlib.sha256(url.encode()).hexdigest(),
        }
    return value


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        selected = default_tool_specs() if specs is None else specs
        self._specs = {spec.name: spec for spec in selected}

    def definitions(self) -> list[dict[str, Any]]:
        return [spec.openai_definition() for spec in self._specs.values()]

    def validate_arguments(self, tool_name: str, raw_arguments: str) -> bool:
        spec = self._specs.get(tool_name)
        if spec is None:
            return False
        try:
            parsed, _ = _normalized_json(raw_arguments)
            spec.arguments_model.model_validate(parsed)
        except (json.JSONDecodeError, ValueError, ValidationError):
            return False
        return True

    async def execute(
        self,
        *,
        session: AsyncSession,
        request_context: RequestContext,
        conversation: Conversation,
        inbound_message: ChannelMessage,
        agent_run: AgentRun,
        call_id: str,
        tool_name: str,
        raw_arguments: str,
        settings: Settings | None = None,
        orchestration_task: OrchestrationTask | None = None,
    ) -> ToolExecutionOutcome:
        spec = self._specs.get(tool_name)
        version = spec.version if spec else "unknown"
        risk_level = spec.risk_level if spec else "R0"
        try:
            parsed, arguments_hash = _normalized_json(raw_arguments)
        except (json.JSONDecodeError, ValueError):
            parsed = {}
            arguments_hash = hashlib.sha256(raw_arguments.encode()).hexdigest()
        key_material = ":".join([str(agent_run.id), call_id, tool_name, version, arguments_hash])
        idempotency_key = hashlib.sha256(key_material.encode()).hexdigest()
        existing = await session.scalar(
            select(ToolExecution).where(
                ToolExecution.workspace_id == request_context.workspace_id,
                or_(
                    ToolExecution.idempotency_key == idempotency_key,
                    (
                        (ToolExecution.agent_run_id == agent_run.id)
                        & (ToolExecution.tool_name == tool_name)
                        & (ToolExecution.tool_version == version)
                        & (ToolExecution.arguments_hash == arguments_hash)
                    ),
                ),
                ToolExecution.status.in_(["completed", "failed"]),
            )
        )
        execution: ToolExecution
        if existing is not None and existing.result is not None:
            previous_envelope = ToolEnvelope.model_validate(existing.result)
            if not previous_envelope.retryable:
                return ToolExecutionOutcome(
                    envelope=previous_envelope,
                    execution_id=existing.id,
                    name=existing.tool_name,
                    risk_level=existing.risk_level,
                    replayed=True,
                )
            if existing.idempotency_key == idempotency_key:
                execution = existing
                execution.status = "running"
                execution.result = None
                execution.error_code = None
                execution.completed_at = None
            else:
                execution = ToolExecution(
                    workspace_id=request_context.workspace_id,
                    conversation_id=conversation.id,
                    agent_run_id=agent_run.id,
                    orchestration_task_id=(
                        orchestration_task.id if orchestration_task is not None else None
                    ),
                    call_id=call_id[:200],
                    tool_name=tool_name[:100],
                    tool_version=version,
                    risk_level=risk_level,
                    arguments_hash=arguments_hash,
                    sanitized_arguments=_sanitized_arguments(tool_name, parsed),
                    status="running",
                    idempotency_key=idempotency_key,
                )
                session.add(execution)
        else:
            execution = ToolExecution(
                workspace_id=request_context.workspace_id,
                conversation_id=conversation.id,
                agent_run_id=agent_run.id,
                orchestration_task_id=(
                    orchestration_task.id if orchestration_task is not None else None
                ),
                call_id=call_id[:200],
                tool_name=tool_name[:100],
                tool_version=version,
                risk_level=risk_level,
                arguments_hash=arguments_hash,
                sanitized_arguments=_sanitized_arguments(tool_name, parsed),
                status="running",
                idempotency_key=idempotency_key,
            )
            session.add(execution)
        await session.flush()
        envelope: ToolEnvelope
        if spec is None:
            envelope = _failure("unknown_tool", "A ferramenta solicitada não existe.")
        else:
            try:
                validated = spec.arguments_model.model_validate(parsed)
            except ValidationError:
                envelope = _failure(
                    "invalid_tool_arguments",
                    "Os argumentos da ferramenta não correspondem ao contrato esperado.",
                )
            else:
                context = ToolContext(
                    session=session,
                    request_context=request_context,
                    conversation=conversation,
                    inbound_message=inbound_message,
                    agent_run=agent_run,
                    call_id=call_id,
                    idempotency_key=idempotency_key,
                    settings=settings or get_settings(),
                    orchestration_task=orchestration_task,
                )
                try:
                    async with session.begin_nested():
                        envelope = await spec.handler(context, validated)
                except AppError as exc:
                    envelope = _failure(exc.code, exc.message, retryable=exc.status_code >= 500)
                except Exception:
                    logger.exception("tool_execution_failed", extra={"tool_name": tool_name})
                    envelope = _failure(
                        "tool_execution_failed",
                        "A ferramenta falhou de forma segura.",
                        retryable=True,
                    )

        execution.result = _sanitized_result(envelope)
        execution.status = "completed" if envelope.ok else "failed"
        execution.error_code = None if envelope.ok else envelope.code
        execution.completed_at = datetime.now(UTC)
        await session.commit()
        return ToolExecutionOutcome(
            envelope=envelope,
            execution_id=execution.id,
            name=tool_name,
            risk_level=risk_level,
            replayed=False,
        )
