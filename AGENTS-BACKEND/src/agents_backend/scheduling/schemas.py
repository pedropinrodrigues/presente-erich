from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from pydantic import Field, model_validator

from agents_backend.conversation.tools import ToolArguments


class StrictModel(ToolArguments):
    pass


class ScheduleTrigger(StrictModel):
    kind: Literal["once", "recurring"]
    timezone: str = Field(min_length=1, max_length=100)
    starts_at: datetime
    recurrence_rule: str | None = Field(default=None, min_length=1, max_length=1000)
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def validate_trigger(self) -> ScheduleTrigger:
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone deve ser um identificador IANA válido") from exc
        if self.starts_at.tzinfo is None:
            raise ValueError("starts_at precisa conter offset de timezone")
        local_start = self.starts_at.astimezone(zone)
        if self.ends_at is not None:
            if self.ends_at.tzinfo is None:
                raise ValueError("ends_at precisa conter offset de timezone")
            if self.ends_at <= self.starts_at:
                raise ValueError("ends_at deve ser posterior a starts_at")
        if self.kind == "once" and self.recurrence_rule is not None:
            raise ValueError("uma execução pontual não aceita recurrence_rule")
        if self.kind == "recurring" and self.recurrence_rule is None:
            raise ValueError("uma execução recorrente exige recurrence_rule")
        if self.recurrence_rule is not None:
            normalized = self.recurrence_rule.removeprefix("RRULE:").strip().upper()
            if "FREQ=SECONDLY" in normalized:
                raise ValueError("recorrência por segundo não é permitida")
            try:
                rule = rrulestr(normalized, dtstart=local_start)
                first = rule.after(local_start, inc=True)
                second = rule.after(first, inc=False) if first is not None else None
            except (TypeError, ValueError) as exc:
                raise ValueError("recurrence_rule inválida") from exc
            if first is None:
                raise ValueError("recurrence_rule não produz ocorrências")
            if second is not None and (second - first).total_seconds() < 300:
                raise ValueError("a frequência mínima é de cinco minutos")
        return self


class ScheduleContextPolicy(StrictModel):
    user_profile: bool = True
    long_term_memory: bool = True
    maximum_memory_queries: int = Field(default=8, ge=0, le=20)


class ScheduleConstraints(StrictModel):
    recipient_emails: list[str] = Field(default_factory=list, max_length=50)
    to_numbers: list[str] = Field(default_factory=list, max_length=50)
    maximum_external_writes_per_run: int = Field(default=1, ge=0, le=20)
    allow_attachments: bool = False


class ScheduleToolPolicy(StrictModel):
    tools: list[str] = Field(min_length=1, max_length=50)
    account_scope: Literal["all_connected_accounts", "specific_accounts", "primary"]
    account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    max_risk: Literal["R0", "R1", "R2"] = "R0"
    constraints: ScheduleConstraints = Field(default_factory=ScheduleConstraints)

    @model_validator(mode="after")
    def validate_accounts(self) -> ScheduleToolPolicy:
        if self.account_scope == "specific_accounts" and not self.account_ids:
            raise ValueError("specific_accounts exige ao menos um account_id")
        return self


class ScheduleDelivery(StrictModel):
    kind: Literal["originating_conversation"] = "originating_conversation"
    send_when_empty: bool = True


class ScheduleSpec(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    trigger: ScheduleTrigger
    context_policy: ScheduleContextPolicy
    tool_policy: ScheduleToolPolicy
    delivery: ScheduleDelivery
    misfire_policy: Literal["latest", "skip"] = "latest"
    misfire_grace_seconds: int = Field(default=21600, ge=0, le=604800)
    max_runs: int | None = Field(default=None, ge=1, le=1_000_000)


class CreateScheduleArguments(StrictModel):
    spec: ScheduleSpec


class ScheduleIdentifierArguments(StrictModel):
    schedule_id: uuid.UUID


class ListSchedulesArguments(StrictModel):
    include_inactive: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class UpdateScheduleArguments(StrictModel):
    schedule_id: uuid.UUID
    spec: ScheduleSpec


class ListScheduleRunsArguments(StrictModel):
    schedule_id: uuid.UUID
    limit: int = Field(default=10, ge=1, le=50)
