from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TranscriptEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_id: uuid.UUID
    source: str = Field(min_length=1, max_length=50)
    captured_at: datetime
    transcript: str = Field(min_length=1, max_length=500_000)
    duration_seconds: float | None = Field(default=None, ge=0)
    language: str = Field(default="pt-BR", min_length=2, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at deve conter fuso horário")
        return value

    @field_validator("transcript")
    @classmethod
    def require_non_blank_transcript(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("transcript não pode ser vazio")
        return value


class IngestTranscriptResponse(BaseModel):
    source_id: uuid.UUID
    status: str
    idempotent_replay: bool


class SourceResponse(BaseModel):
    id: uuid.UUID
    capture_id: uuid.UUID
    source: str
    captured_at: datetime
    transcript: str
    duration_seconds: float | None
    language: str
    metadata: dict[str, Any]
    status: str
    created_at: datetime


class AskContext(BaseModel):
    entity_ids: list[uuid.UUID] = Field(default_factory=list)
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None


class AskMemoryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)
    context: AskContext = Field(default_factory=AskContext)


class EvidenceResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    excerpt: str
    fact_id: uuid.UUID | None = None
    commitment_id: uuid.UUID | None = None


class AskMemoryResponse(BaseModel):
    answer: str
    evidence: list[EvidenceResponse]
    uncertainties: list[str]
    source_ids: list[uuid.UUID]


class SearchItem(BaseModel):
    id: uuid.UUID
    type: Literal["entity", "fact", "commitment"]
    title: str
    content: str
    status: str
    confidence: float | None = None
    occurred_at: datetime
    evidence: list[EvidenceResponse] = Field(default_factory=list)


class SearchMemoryResponse(BaseModel):
    items: list[SearchItem]
    next_cursor: str | None = None
    total: int | None = None


class ContextProfileItem(BaseModel):
    label: str
    detail: str
    updated_at: datetime


class UserContextProfileResponse(BaseModel):
    """Bounded derived context, never a substitute for factual memory retrieval."""

    summary: str
    entities: list[ContextProfileItem] = Field(default_factory=list)
    current_facts: list[ContextProfileItem] = Field(default_factory=list)
    open_commitments: list[ContextProfileItem] = Field(default_factory=list)
    updated_at: datetime | None = None


class EntityResponse(BaseModel):
    id: uuid.UUID
    type: str
    canonical_name: str
    aliases: list[str]
    facts: list[SearchItem]
    commitments: list[SearchItem]
    history: list[SearchItem]
    next_cursor: str | None = None


class CorrectionRequest(BaseModel):
    target_id: uuid.UUID
    target_type: Literal["fact", "commitment"]
    operation: Literal["replace", "dispute", "delete"]
    value: dict[str, Any] | str | None = None
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def replacement_requires_value(cls, value: object, info: object) -> object:
        return value


class MutationResponse(BaseModel):
    target_id: uuid.UUID
    status: str
    audit_event_id: uuid.UUID


class ExtractionEvidence(BaseModel):
    excerpt: str = Field(min_length=1)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)


class EntityCandidate(BaseModel):
    candidate_id: str
    entity_type: Literal["person", "organization", "project"]
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence: ExtractionEvidence


class FactCandidate(BaseModel):
    candidate_id: str
    subject_candidate_id: str | None = None
    fact_type: str
    predicate: str
    value: str
    value_text: str
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime | None = None
    supersedes_predicate: str | None = None
    evidence: ExtractionEvidence


class CommitmentCandidate(BaseModel):
    candidate_id: str
    responsible_candidate_id: str | None = None
    description: str
    due_at: datetime | None = None
    status: Literal["open", "completed", "cancelled"] = "open"
    confidence: float = Field(ge=0, le=1)
    evidence: ExtractionEvidence


class ExtractionResult(BaseModel):
    entities: list[EntityCandidate]
    facts: list[FactCandidate]
    commitments: list[CommitmentCandidate]


class AgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def require_non_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message não pode ser vazio")
        return value


class AgentToolUseResponse(BaseModel):
    name: str
    status: str
    risk_level: str
    idempotent_replay: bool = False


class ConversationRouteDecision(BaseModel):
    """Structured, auditable decision produced by the fast conversation model."""

    model_config = ConfigDict(extra="forbid")

    route: Literal["answer", "delegate", "clarify", "request_confirmation"]
    understanding: str = Field(min_length=1, max_length=1000)
    handoff_context: str = Field(min_length=1, max_length=5000)
    orchestration_intent: (
        Literal[
            "memory_write",
            "memory_correction",
            "memory_deletion",
            "automation",
            "external_communication",
            "account_management",
            "invite_management",
            "web_research",
            "compound",
        ]
        | None
    ) = None
    user_message: str | None = Field(default=None, min_length=1, max_length=2000)
    acknowledgement: str | None = Field(default=None, min_length=1, max_length=240)
    answer_message: str | None = Field(default=None, min_length=1, max_length=2000)
    read_operation: (
        Literal["search_memory", "list_open_commitments", "get_pending_action"] | None
    ) = None
    read_query: str | None = Field(default=None, max_length=1000)
    read_item_type: Literal["entity", "fact", "commitment"] | None = None
    read_status: str | None = Field(default=None, max_length=100)
    read_limit: int | None = Field(default=None, ge=1, le=20)
    confirmation_status: Literal["none", "explicit", "ambiguous", "cancellation"]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_route_fields(self) -> ConversationRouteDecision:
        if self.route == "delegate" and self.orchestration_intent is None:
            raise ValueError("orchestration_intent é obrigatório para delegação")
        if self.route != "delegate" and self.orchestration_intent is not None:
            raise ValueError("orchestration_intent só pode ser usado em delegação")
        if self.route in {"clarify", "request_confirmation"} and not self.user_message:
            raise ValueError("user_message é obrigatório nesta rota")
        if self.route in {"answer", "delegate"} and self.user_message is not None:
            raise ValueError("user_message não pode ser usado nesta rota")
        if self.route == "delegate" and self.acknowledgement is None:
            raise ValueError("acknowledgement é obrigatório para delegação")
        if self.route != "delegate" and self.acknowledgement is not None:
            raise ValueError("acknowledgement só pode ser usado em delegação")
        has_read = self.read_operation is not None
        read_fields_present = any(
            value is not None
            for value in (self.read_query, self.read_item_type, self.read_status, self.read_limit)
        )
        if self.route != "answer" and (
            self.answer_message is not None or has_read or read_fields_present
        ):
            raise ValueError("resposta ou leitura só pode ser usada na rota answer")
        if self.route == "answer" and has_read == (self.answer_message is not None):
            raise ValueError("answer exige uma resposta direta ou uma única operação de leitura")
        if not has_read and read_fields_present:
            raise ValueError("parâmetros de leitura exigem uma operação de leitura")
        if self.read_operation == "search_memory" and not self.read_query:
            raise ValueError("search_memory exige read_query")
        if self.read_operation != "search_memory" and (
            self.read_item_type is not None or self.read_status is not None
        ):
            raise ValueError("filtros de item só são válidos para search_memory")
        if self.route == "request_confirmation" and self.confirmation_status != "ambiguous":
            raise ValueError("request_confirmation exige confirmação ambígua")
        return self


class PendingActionResponse(BaseModel):
    id: uuid.UUID
    summary: str
    status: str
    expires_at: datetime


class AgentTurnResponse(BaseModel):
    conversation_id: uuid.UUID
    message_id: str
    answer: str
    tools_used: list[AgentToolUseResponse] = Field(default_factory=list)
    pending_action: PendingActionResponse | None = None
    orchestration_task_id: uuid.UUID | None = None
    idempotent_replay: bool = False


class OrchestrationTaskResponse(BaseModel):
    id: uuid.UUID
    intent: str
    status: str
    summary: str
    routing_context: dict[str, Any]
    result_code: str | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class WhatsappAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number: str = Field(min_length=8, max_length=30)
    display_name: str | None = Field(default=None, max_length=200)


class WhatsappAccountResponse(BaseModel):
    id: uuid.UUID
    phone_number: str
    display_name: str | None
    active: bool
    verified_at: datetime | None
    verification_phrase: str | None = None
    verification_expires_at: datetime | None = None


class TelegramAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=200)


class TelegramAccountResponse(BaseModel):
    id: uuid.UUID
    bot_username: str
    display_name: str | None
    active: bool
    verified_at: datetime | None
    verification_deep_link: str | None = None
    verification_expires_at: datetime | None = None
