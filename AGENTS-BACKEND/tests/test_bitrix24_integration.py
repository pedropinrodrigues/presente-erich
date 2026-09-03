from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_records, conversation_settings

from agents_backend.auth import RequestContext
from agents_backend.conversation.tools import ToolContext, ToolRegistry
from agents_backend.integrations.bitrix24.crypto import decrypt_secret
from agents_backend.integrations.bitrix24.gateway import Bitrix24Gateway
from agents_backend.integrations.bitrix24.policies import POLICIES, EmptyArguments
from agents_backend.integrations.bitrix24.results import normalize_result
from agents_backend.integrations.bitrix24.service import (
    activate_pending_connection,
    bitrix24_tool_specs,
    connect_bitrix24,
    expire_stale_connections,
    submit_connection_token,
)
from agents_backend.models import (
    ExternalConnectionRequest,
    ExternalIntegration,
    OrchestrationIntent,
    PendingAction,
)
from agents_backend.orchestration.policies import capabilities_for_intent


def bitrix_settings():
    return conversation_settings(
        BITRIX24_MCP_ENABLED="true",
        BITRIX24_PUBLIC_BASE_URL="https://api.example.com",
        BITRIX24_CREDENTIAL_ENCRYPTION_KEY="test-only-bitrix-key",
        BITRIX24_TOOL_SEARCH_DEALS="crm.deal.search",
        BITRIX24_TOOL_UPDATE_DEAL="crm.deal.update",
        BITRIX24_TOOL_LIST_TASKS="task.list",
        BITRIX24_TOOL_CREATE_TASK="task.create",
    )


def test_all_bitrix_writes_require_confirmation() -> None:
    assert {policy.name for policy in POLICIES if policy.risk == "R2"} == {
        "bitrix_update_deal",
        "bitrix_create_task",
        "bitrix_update_task",
    }


def test_bitrix_catalog_is_scoped_and_strict() -> None:
    settings = bitrix_settings()
    communication = bitrix24_tool_specs(
        capabilities_for_intent(OrchestrationIntent.EXTERNAL_COMMUNICATION), settings
    )
    automation = bitrix24_tool_specs(
        capabilities_for_intent(OrchestrationIntent.AUTOMATION), settings
    )
    communication_names = {spec.name for spec in communication}
    automation_names = {spec.name for spec in automation}

    assert "bitrix_search_deals" in communication_names
    assert "bitrix_update_deal" in communication_names
    assert "bitrix_list_tasks" not in communication_names
    assert "bitrix_list_tasks" in automation_names
    assert "bitrix_create_task" in automation_names
    assert "bitrix_update_task" not in automation_names  # slug intentionally absent

    for definition in ToolRegistry(communication).definitions():
        schema = definition["parameters"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])


def test_bitrix_result_redacts_nested_credentials() -> None:
    result = normalize_result(
        {
            "content": [
                {
                    "type": "text",
                    "text": '{"deal":{"id":"10","token":"secret","title":"Atlas"}}',
                }
            ]
        }
    )

    assert result == {"deal": {"id": "10", "token": "[redacted]", "title": "Atlas"}}


@pytest.mark.asyncio
async def test_token_flow_encrypts_then_requires_channel_confirmation(
    session: AsyncSession,
    context: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = bitrix_settings()
    conversation, message, run = await conversation_records(
        session, context, content="conecte meu Bitrix24"
    )
    tool_context = ToolContext(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=message,
        agent_run=run,
        call_id="connect-1",
        idempotency_key="connect-1",
        settings=settings,
    )

    outcome = await connect_bitrix24(tool_context, EmptyArguments())
    assert outcome.ok is True
    url = outcome.data["authorization_url"]
    assert "?" not in url
    state = url.split("#state=", 1)[1]

    credential = secrets.token_urlsafe(32)

    async def fake_list_tools(_: Bitrix24Gateway, token: str):
        assert token == credential
        return [{"name": "crm.deal.search"}, {"name": "task.list"}]

    monkeypatch.setattr(Bitrix24Gateway, "list_tools", fake_list_tools)
    ok, message_text = await submit_connection_token(session, settings, state, credential)
    assert ok is True
    assert "confirmo" in message_text

    integration = await session.scalar(
        select(ExternalIntegration).where(ExternalIntegration.provider == "bitrix24")
    )
    assert integration is not None
    assert integration.status == "awaiting_confirmation"
    assert credential not in str(integration.credential_ciphertext)
    assert decrypt_secret(settings, str(integration.credential_ciphertext)) == credential
    request = await session.scalar(
        select(ExternalConnectionRequest).where(ExternalConnectionRequest.provider == "bitrix24")
    )
    pending = await session.scalar(
        select(PendingAction).where(PendingAction.tool_name == "activate_bitrix_connection")
    )
    assert request is not None and request.status == "awaiting_confirmation"
    assert pending is not None and pending.status == "pending"

    confirmation_message, confirmation_inbound, confirmation_run = (
        conversation,
        message,
        run,
    )
    confirmation_context = ToolContext(
        session=session,
        request_context=context,
        conversation=confirmation_message,
        inbound_message=confirmation_inbound,
        agent_run=confirmation_run,
        call_id="confirm-1",
        idempotency_key=str(uuid.uuid4()),
        settings=settings,
    )
    activated = await activate_pending_connection(confirmation_context, pending)
    assert activated.ok is True
    assert integration.status == "active"
    assert request.status == "completed"

    integration.status = "awaiting_confirmation"
    integration.credential_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    request.status = "awaiting_confirmation"
    request.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    pending.status = "pending"
    pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await expire_stale_connections(session) is True
    assert integration.status == "expired"
    assert integration.credential_ciphertext is None
    assert request.status == "expired"
    assert pending.status == "expired"
