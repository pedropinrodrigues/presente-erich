from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from agents_backend.config import Settings
from agents_backend.conversation.tools import ToolArguments


class EmptyArguments(ToolArguments):
    pass


class SearchDealsArguments(ToolArguments):
    query: str | None = Field(default=None, max_length=300)
    stage_id: str | None = Field(default=None, max_length=100)
    assigned_by_id: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=10, ge=1, le=50)


class GetDealArguments(ToolArguments):
    deal_id: str = Field(min_length=1, max_length=100)


class UpdateDealArguments(ToolArguments):
    deal_id: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    stage_id: str | None = Field(default=None, min_length=1, max_length=100)
    assigned_by_id: str | None = Field(default=None, min_length=1, max_length=100)
    opportunity: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_change(self) -> UpdateDealArguments:
        if all(
            value is None
            for value in (self.title, self.stage_id, self.assigned_by_id, self.opportunity)
        ):
            raise ValueError("informe ao menos um campo para alterar")
        return self


class ListTasksArguments(ToolArguments):
    query: str | None = Field(default=None, max_length=300)
    status: str | None = Field(default=None, max_length=100)
    responsible_id: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=10, ge=1, le=50)


class GetTaskArguments(ToolArguments):
    task_id: str = Field(min_length=1, max_length=100)


class CreateTaskArguments(ToolArguments):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    deadline: str | None = Field(default=None, max_length=80)
    responsible_id: str | None = Field(default=None, max_length=100)


class UpdateTaskArguments(ToolArguments):
    task_id: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    deadline: str | None = Field(default=None, max_length=80)
    responsible_id: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_change(self) -> UpdateTaskArguments:
        if all(
            value is None
            for value in (
                self.title,
                self.description,
                self.deadline,
                self.responsible_id,
                self.status,
            )
        ):
            raise ValueError("informe ao menos um campo para alterar")
        return self


@dataclass(frozen=True, slots=True)
class BitrixToolPolicy:
    name: str
    setting: str
    description: str
    arguments_model: type[ToolArguments]
    risk: Literal["R0", "R2"]
    capability: str

    def remote_tool(self, settings: Settings) -> str | None:
        return getattr(settings, self.setting)


POLICIES = (
    BitrixToolPolicy(
        "bitrix_search_deals",
        "bitrix24_tool_search_deals",
        "Busca negócios no CRM Bitrix24.",
        SearchDealsArguments,
        "R0",
        "bitrix_crm_read",
    ),
    BitrixToolPolicy(
        "bitrix_get_deal",
        "bitrix24_tool_get_deal",
        "Lê um negócio do CRM Bitrix24 por ID.",
        GetDealArguments,
        "R0",
        "bitrix_crm_read",
    ),
    BitrixToolPolicy(
        "bitrix_update_deal",
        "bitrix24_tool_update_deal",
        "Propõe alterar um negócio no CRM Bitrix24 e exige confirmação.",
        UpdateDealArguments,
        "R2",
        "bitrix_crm_execute",
    ),
    BitrixToolPolicy(
        "bitrix_list_tasks",
        "bitrix24_tool_list_tasks",
        "Lista tarefas no Bitrix24.",
        ListTasksArguments,
        "R0",
        "bitrix_task_read",
    ),
    BitrixToolPolicy(
        "bitrix_get_task",
        "bitrix24_tool_get_task",
        "Lê uma tarefa no Bitrix24 por ID.",
        GetTaskArguments,
        "R0",
        "bitrix_task_read",
    ),
    BitrixToolPolicy(
        "bitrix_create_task",
        "bitrix24_tool_create_task",
        "Propõe criar uma tarefa no Bitrix24 e exige confirmação.",
        CreateTaskArguments,
        "R2",
        "bitrix_task_execute",
    ),
    BitrixToolPolicy(
        "bitrix_update_task",
        "bitrix24_tool_update_task",
        "Propõe alterar uma tarefa no Bitrix24 e exige confirmação.",
        UpdateTaskArguments,
        "R2",
        "bitrix_task_execute",
    ),
)
