from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from agents_backend.conversation.tools import ToolArguments


class ConnectExternalAppArguments(ToolArguments):
    toolkit: Literal["gmail", "googlecalendar", "whatsapp"]
    add_another: bool = Field(
        default=False,
        description="Use true quando o usuário pedir para adicionar outra conta do mesmo serviço.",
    )
    account_label: str | None = Field(default=None, min_length=1, max_length=100)


class GetExternalConnectionStatusArguments(ToolArguments):
    toolkit: Literal["gmail", "googlecalendar", "whatsapp"]
    account_id: UUID | None = None


class ListExternalAccountsArguments(ToolArguments):
    toolkit: Literal["gmail", "googlecalendar", "whatsapp"] | None = None


class ConfigureExternalAccountArguments(ToolArguments):
    account_id: UUID
    account_label: str | None = Field(default=None, min_length=1, max_length=100)
    set_as_primary: bool = False


class AccountScopedArguments(ToolArguments):
    account_id: UUID | None = Field(
        default=None,
        description=(
            "Conta específica retornada por list_external_accounts. Null consulta todas as "
            "contas em leituras e usa a conta padrão em escritas."
        ),
    )


class GmailSearchArguments(AccountScopedArguments):
    query: str | None = Field(default=None, max_length=500)
    max_results: int = Field(default=10, ge=1, le=20)


class GmailGetArguments(AccountScopedArguments):
    message_id: str = Field(min_length=1, max_length=300)


class GmailDraftArguments(AccountScopedArguments):
    recipient_email: str = Field(min_length=3, max_length=1000)
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=100_000)


class GmailSendArguments(GmailDraftArguments):
    pass


class CalendarListArguments(AccountScopedArguments):
    calendar_ids: list[str] | None = Field(
        default=None,
        max_length=50,
        description="IDs específicos ou null para consultar todos os calendários visíveis.",
    )
    start_date: date | None = Field(
        default=None,
        description="Primeira data local incluída, sempre em YYYY-MM-DD; nunca use texto relativo.",
    )
    end_date: date | None = Field(
        default=None,
        description="Data local final exclusiva em YYYY-MM-DD; para um dia, use o dia seguinte.",
    )
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=100)
    query: str | None = Field(default=None, max_length=500)
    max_results: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_date_window(self) -> CalendarListArguments:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date e end_date devem ser informados juntos")
        if self.start_date is not None and self.end_date <= self.start_date:
            raise ValueError("end_date deve ser posterior a start_date")
        ZoneInfo(self.timezone)
        return self


class CalendarFreeArguments(AccountScopedArguments):
    time_min: str = Field(min_length=1, max_length=80)
    time_max: str = Field(min_length=1, max_length=80)
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=100)


class CalendarCreateArguments(AccountScopedArguments):
    summary: str = Field(min_length=1, max_length=1000)
    start_datetime: str = Field(min_length=1, max_length=80)
    end_datetime: str | None = Field(default=None, max_length=80)
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=100)
    attendees: list[str] = Field(default_factory=list, max_length=50)
    description: str | None = Field(default=None, max_length=20_000)


class CalendarUpdateArguments(CalendarCreateArguments):
    event_id: str = Field(min_length=1, max_length=500)


class CalendarDeleteArguments(AccountScopedArguments):
    event_id: str = Field(min_length=1, max_length=500)
    calendar_id: str = Field(default="primary", min_length=1, max_length=300)


class WhatsappPhonesArguments(ToolArguments):
    waba_id: str | None = Field(default=None, max_length=300)


class WhatsappHistoryArguments(ToolArguments):
    phone_number_id: str = Field(min_length=1, max_length=300)


class WhatsappSendArguments(ToolArguments):
    phone_number_id: str = Field(min_length=1, max_length=300)
    to_number: str = Field(min_length=7, max_length=30)
    text: str = Field(min_length=1, max_length=4096)


@dataclass(frozen=True, slots=True)
class ExternalToolPolicy:
    name: str
    toolkit: str
    remote_slug: str
    description: str
    arguments_model: type[ToolArguments]
    risk: Literal["R0", "R1", "R2"]
    capability: str


POLICIES = (
    ExternalToolPolicy(
        "gmail_search_emails",
        "gmail",
        "GMAIL_FETCH_EMAILS",
        "Busca emails. Com account_id=null, consulta todas as contas Gmail conectadas e combina "
        "os resultados; com account_id, limita à conta escolhida. Retorna itens compactos com "
        "message_id, sender, subject, received_at, preview e text_excerpt; HTML bruto é omitido.",
        GmailSearchArguments,
        "R0",
        "integration_read",
    ),
    ExternalToolPolicy(
        "gmail_get_email",
        "gmail",
        "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
        "Lê um email específico por ID. Use account_id quando conhecido; null procura nas contas "
        "Gmail conectadas. Retorna cabeçalhos, preview e trecho de texto limpo.",
        GmailGetArguments,
        "R0",
        "integration_read",
    ),
    ExternalToolPolicy(
        "gmail_create_draft",
        "gmail",
        "GMAIL_CREATE_EMAIL_DRAFT",
        "Cria um rascunho na conta indicada ou na conta Gmail padrão; não envia.",
        GmailDraftArguments,
        "R1",
        "integration_draft",
    ),
    ExternalToolPolicy(
        "gmail_send_email",
        "gmail",
        "GMAIL_SEND_EMAIL",
        "Propõe o envio pela conta indicada ou pela conta Gmail padrão e exige confirmação.",
        GmailSendArguments,
        "R2",
        "integration_execute",
    ),
    ExternalToolPolicy(
        "calendar_list_events",
        "googlecalendar",
        "GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS",
        "Lista eventos de todas as contas Google Calendar conectadas quando account_id=null. Em "
        "cada conta inclui calendários secundários e compartilhados. Informe as datas somente "
        "em YYYY-MM-DD; end_date é exclusiva. Para consultar um único dia, use esse dia em "
        "start_date e o dia seguinte em end_date. Use calendar_ids=null para uma visão geral. "
        "Nunca envie today, tomorrow, ontem, hoje, amanhã ou horários neste contrato.",
        CalendarListArguments,
        "R0",
        "integration_read",
    ),
    ExternalToolPolicy(
        "calendar_find_free_slots",
        "googlecalendar",
        "GOOGLECALENDAR_FIND_FREE_SLOTS",
        "Consulta horários livres no Google Calendar.",
        CalendarFreeArguments,
        "R0",
        "integration_read",
    ),
    ExternalToolPolicy(
        "calendar_create_event",
        "googlecalendar",
        "GOOGLECALENDAR_CREATE_EVENT",
        "Propõe criar um evento e exige confirmação.",
        CalendarCreateArguments,
        "R2",
        "integration_execute",
    ),
    ExternalToolPolicy(
        "calendar_update_event",
        "googlecalendar",
        "GOOGLECALENDAR_UPDATE_EVENT",
        "Propõe alterar um evento e exige confirmação.",
        CalendarUpdateArguments,
        "R2",
        "integration_execute",
    ),
    ExternalToolPolicy(
        "calendar_delete_event",
        "googlecalendar",
        "GOOGLECALENDAR_DELETE_EVENT",
        "Propõe cancelar um evento e exige confirmação.",
        CalendarDeleteArguments,
        "R2",
        "integration_execute",
    ),
    ExternalToolPolicy(
        "whatsapp_list_phone_numbers",
        "whatsapp",
        "WHATSAPP_GET_PHONE_NUMBERS",
        "Lista números da conta WhatsApp Business conectada.",
        WhatsappPhonesArguments,
        "R0",
        "integration_read",
    ),
    ExternalToolPolicy(
        "whatsapp_get_message_history",
        "whatsapp",
        "WHATSAPP_GET_MESSAGE_HISTORY",
        "Consulta histórico do WhatsApp Business.",
        WhatsappHistoryArguments,
        "R0",
        "integration_read",
    ),
    ExternalToolPolicy(
        "whatsapp_send_message",
        "whatsapp",
        "WHATSAPP_SEND_MESSAGE",
        "Propõe enviar mensagem pelo WhatsApp e exige confirmação.",
        WhatsappSendArguments,
        "R2",
        "integration_execute",
    ),
)


def remote_arguments(policy: ExternalToolPolicy, arguments: dict[str, object]) -> dict[str, object]:
    data = {key: value for key, value in arguments.items() if value is not None}
    data.pop("account_id", None)
    if policy.name == "calendar_list_events":
        timezone = str(data.pop("timezone"))
        zone = ZoneInfo(timezone)
        start_date = data.pop("start_date", None)
        end_date = data.pop("end_date", None)
        if isinstance(start_date, str):
            start_date = date.fromisoformat(start_date)
        if isinstance(end_date, str):
            end_date = date.fromisoformat(end_date)
        remote: dict[str, object] = {
            "calendar_ids": data.pop("calendar_ids", None),
            "q": data.pop("query", None),
            "max_results_per_calendar": data.pop("max_results"),
            "show_deleted": False,
            "single_events": True,
            "response_detail": "minimal",
        }
        if isinstance(start_date, date) and isinstance(end_date, date):
            remote["time_min"] = datetime.combine(start_date, time.min, zone).isoformat()
            remote["time_max"] = datetime.combine(end_date, time.min, zone).isoformat()
        return {key: value for key, value in remote.items() if value is not None}
    if policy.name == "calendar_delete_event":
        return {"event_id": data["event_id"], "calendar_id": data["calendar_id"]}
    return data
