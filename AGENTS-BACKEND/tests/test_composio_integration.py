from __future__ import annotations

import json
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from agents_backend.conversation.tools import ToolEnvelope, ToolRegistry
from agents_backend.integrations.composio.gateway import ComposioGateway
from agents_backend.integrations.composio.policies import (
    POLICIES,
    CalendarListArguments,
    ConfigureExternalAccountArguments,
    remote_arguments,
)
from agents_backend.integrations.composio.results import (
    bound_normalized_result,
    normalize_mcp_result,
)
from agents_backend.integrations.composio.service import (
    _merged_multi_account_read,
    composio_tool_specs,
    configure_external_account,
)
from agents_backend.models import ExternalIntegration, OrchestrationIntent
from agents_backend.orchestration.policies import capabilities_for_intent


def test_every_external_write_requires_confirmation() -> None:
    mutating = {
        "GMAIL_SEND_EMAIL",
        "GOOGLECALENDAR_CREATE_EVENT",
        "GOOGLECALENDAR_UPDATE_EVENT",
        "GOOGLECALENDAR_DELETE_EVENT",
        "WHATSAPP_SEND_MESSAGE",
    }

    assert {policy.remote_slug for policy in POLICIES if policy.risk == "R2"} == mutating


def test_automation_and_communication_receive_only_scoped_composio_tools() -> None:
    automation = {
        spec.name
        for spec in composio_tool_specs(capabilities_for_intent(OrchestrationIntent.AUTOMATION))
    }
    communication = {
        spec.name
        for spec in composio_tool_specs(
            capabilities_for_intent(OrchestrationIntent.EXTERNAL_COMMUNICATION)
        )
    }

    assert "calendar_create_event" in automation
    assert "gmail_create_draft" not in automation
    assert "gmail_send_email" in communication
    assert "whatsapp_send_message" in communication
    assert "connect_external_app" in automation & communication
    assert "get_external_connection_status" in automation & communication


def test_account_management_can_verify_then_read_connected_account() -> None:
    names = {
        spec.name
        for spec in composio_tool_specs(
            capabilities_for_intent(OrchestrationIntent.ACCOUNT_MANAGEMENT)
        )
    }

    assert "get_external_connection_status" in names
    assert "list_external_accounts" in names
    assert "configure_external_account" in names
    assert "gmail_search_emails" in names


def test_composio_function_schemas_are_strict_openai_schemas() -> None:
    definitions = ToolRegistry(
        composio_tool_specs(capabilities_for_intent(OrchestrationIntent.EXTERNAL_COMMUNICATION))
    ).definitions()

    for definition in definitions:
        parameters = definition["parameters"]
        assert parameters["required"] == list(parameters["properties"])
        assert parameters["additionalProperties"] is False
        assert "default" not in str(parameters)


def test_calendar_list_arguments_are_mapped_to_remote_contract() -> None:
    policy = next(policy for policy in POLICIES if policy.name == "calendar_list_events")
    arguments = CalendarListArguments(
        calendar_ids=None,
        start_date=date(2026, 8, 24),
        end_date=date(2026, 8, 25),
        timezone="America/Sao_Paulo",
        max_results=5,
    )

    assert remote_arguments(policy, arguments.model_dump(mode="json")) == {
        "time_min": "2026-08-24T00:00:00-03:00",
        "time_max": "2026-08-25T00:00:00-03:00",
        "single_events": True,
        "show_deleted": False,
        "response_detail": "full",
        "max_results_per_calendar": 5,
    }


def test_account_selector_is_not_forwarded_to_remote_provider() -> None:
    policy = next(policy for policy in POLICIES if policy.name == "gmail_search_emails")

    assert remote_arguments(
        policy,
        {
            "account_id": str(uuid.uuid4()),
            "query": "after:2026/08/20",
            "max_results": 10,
        },
    ) == {"query": "after:2026/08/20", "max_results": 10}


def test_calendar_list_rejects_relative_or_invalid_date_windows() -> None:
    with pytest.raises(ValidationError):
        CalendarListArguments(start_date="tomorrow", end_date="day after tomorrow")

    with pytest.raises(ValidationError):
        CalendarListArguments(start_date="2026-08-24", end_date="2026-08-24")


def test_gmail_mcp_result_drops_raw_html_before_agent_context() -> None:
    messages = [
        {
            "messageId": f"message-{index}",
            "threadId": f"thread-{index}",
            "sender": "Sender <sender@example.com>",
            "to": "user@example.com",
            "subject": f"Subject {index}",
            "messageTimestamp": "2026-08-20T01:11:00Z",
            "preview": {"body": "A short preview", "subject": f"Subject {index}"},
            "messageText": "<html><body>Useful content " + ("x" * 100_000) + "</body></html>",
            "payload": {"parts": [{"body": "raw" * 100_000}]},
            "labelIds": ["INBOX"],
        }
        for index in range(10)
    ]
    raw = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "data": {
                            "messages": messages,
                            "resultSizeEstimate": 10,
                            "nextPageToken": "next",
                        },
                        "successful": True,
                        "error": None,
                    }
                ),
            }
        ],
        "isError": False,
    }

    result = bound_normalized_result(normalize_mcp_result("GMAIL_FETCH_EMAILS", raw))
    encoded = json.dumps(result)

    assert result["count_returned"] == 10
    assert result["messages"][0]["subject"] == "Subject 0"
    assert len(result["messages"][0]["text_excerpt"]) <= 1200
    assert "payload" not in encoded
    assert "messageText" not in encoded
    assert "truncated" not in encoded
    assert len(encoded) < 60_000


def test_calendar_mcp_result_keeps_events_and_drops_provider_noise() -> None:
    raw = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "data": {
                            "timeZone": "America/Sao_Paulo",
                            "etag": "provider-noise",
                            "items": [
                                {
                                    "id": "event-1",
                                    "summary": "Planejamento",
                                    "start": {
                                        "dateTime": "2026-08-24T09:00:00-03:00",
                                        "timeZone": "America/Sao_Paulo",
                                    },
                                    "end": {
                                        "dateTime": "2026-08-24T10:00:00-03:00",
                                        "timeZone": "America/Sao_Paulo",
                                    },
                                    "description": "Revisar prioridades",
                                    "organizer": {"email": "owner@example.com"},
                                    "htmlLink": "https://calendar.google.com/event-1",
                                    "status": "confirmed",
                                    "etag": "event-noise",
                                    "reminders": {"useDefault": True},
                                }
                            ],
                        },
                        "successful": True,
                        "error": None,
                    }
                ),
            }
        ],
        "isError": False,
    }

    result = normalize_mcp_result("GOOGLECALENDAR_EVENTS_LIST", raw)
    encoded = json.dumps(result)

    assert result["count_returned"] == 1
    assert result["events"][0]["title"] == "Planejamento"
    assert result["events"][0]["start"]["dateTime"] == "2026-08-24T09:00:00-03:00"
    assert "provider-noise" not in encoded
    assert "event-noise" not in encoded
    assert "reminders" not in encoded


def test_all_calendars_result_uses_compact_summary_view() -> None:
    raw = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "data": {
                            "calendars_queried": [
                                {"id": "primary@example.com", "summary": "Principal"},
                                {"id": "family", "summary": "Família"},
                            ],
                            "summary_view": [
                                {
                                    "calendar": "Família",
                                    "display_url": "https://calendar.google.com/event-2",
                                    "end": "2026-08-24T15:00:00-03:00",
                                    "event_id": "event-2",
                                    "is_all_day": False,
                                    "start": "2026-08-24T14:00:00-03:00",
                                    "title": "Compromisso familiar",
                                }
                            ],
                            "events": [],
                            "errors_by_calendar": {},
                        },
                        "successful": True,
                    }
                ),
            }
        ],
        "isError": False,
    }

    result = normalize_mcp_result("GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS", raw)

    assert result["count_returned"] == 1
    assert len(result["calendars_queried"]) == 2
    assert result["events"][0]["calendar"] == "Família"
    assert result["events"][0]["title"] == "Compromisso familiar"


def test_multi_account_gmail_results_are_merged_sorted_and_limited() -> None:
    policy = next(policy for policy in POLICIES if policy.name == "gmail_search_emails")
    personal = ExternalIntegration(
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        toolkit_slug="gmail",
        auth_config_id="personal-auth",
        connected_account_id="personal-connected",
        status="active",
        account_label="Pessoal",
        is_default=True,
    )
    work = ExternalIntegration(
        workspace_id=personal.workspace_id,
        user_id=personal.user_id,
        toolkit_slug="gmail",
        auth_config_id="work-auth",
        connected_account_id="work-connected",
        status="active",
        account_label="Trabalho",
        is_default=False,
    )
    results = [
        (
            personal,
            ToolEnvelope(
                ok=True,
                code="ok",
                message="ok",
                data={"messages": [{"subject": "Antigo", "received_at": "2026-08-20"}]},
            ),
        ),
        (
            work,
            ToolEnvelope(
                ok=True,
                code="ok",
                message="ok",
                data={"messages": [{"subject": "Novo", "received_at": "2026-08-22"}]},
            ),
        ),
    ]

    merged = _merged_multi_account_read(policy, {"max_results": 1}, results)

    assert merged.ok is True
    assert merged.data["accounts_queried"] == 2
    assert merged.data["messages"][0]["subject"] == "Novo"
    assert merged.data["messages"][0]["account"]["label"] == "Trabalho"
    assert merged.data["count_returned"] == 1


@pytest.mark.asyncio
async def test_configure_external_account_changes_primary_account(session, context) -> None:
    personal = ExternalIntegration(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        toolkit_slug="gmail",
        auth_config_id="personal-auth",
        connected_account_id="personal-connected",
        status="active",
        account_label="Pessoal",
        is_default=True,
    )
    work = ExternalIntegration(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        toolkit_slug="gmail",
        auth_config_id="work-auth",
        connected_account_id="work-connected",
        status="active",
        is_default=False,
    )
    session.add_all([personal, work])
    await session.flush()

    result = await configure_external_account(
        SimpleNamespace(session=session, request_context=context),
        ConfigureExternalAccountArguments(
            account_id=work.id,
            account_label="Trabalho",
            set_as_primary=True,
        ),
    )
    await session.refresh(personal)
    await session.refresh(work)

    assert result.ok is True
    assert personal.is_default is False
    assert work.is_default is True
    assert work.account_label == "Trabalho"


@pytest.mark.asyncio
async def test_create_link_enables_multiple_accounts_and_passes_unique_alias() -> None:
    gateway = object.__new__(ComposioGateway)
    link = MagicMock(return_value=SimpleNamespace(id="connected-2"))
    gateway.client = SimpleNamespace(connected_accounts=SimpleNamespace(link=link))

    result = await gateway.create_link(
        user_id="opaque-user",
        auth_config_id="calendar-auth",
        callback_url="https://example.com/callback",
        alias="googlecalendar-1234",
        allow_multiple=True,
    )

    assert result.id == "connected-2"
    link.assert_called_once_with(
        "opaque-user",
        "calendar-auth",
        callback_url="https://example.com/callback",
        alias="googlecalendar-1234",
        allow_multiple=True,
    )
