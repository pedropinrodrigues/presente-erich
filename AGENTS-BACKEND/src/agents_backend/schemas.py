from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    idempotent_replay: bool = False


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
