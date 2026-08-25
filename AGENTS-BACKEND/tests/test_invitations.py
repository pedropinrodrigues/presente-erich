from __future__ import annotations

import hashlib
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_records, conversation_settings

from agents_backend.auth import RequestContext
from agents_backend.conversation.telegram_commands import handle_account_command
from agents_backend.conversation.tools import ToolRegistry
from agents_backend.errors import ForbiddenError, NotFoundError
from agents_backend.invitations.service import (
    create_telegram_invite,
    list_telegram_invites,
    revoke_telegram_invite,
)
from agents_backend.models import ChannelInvite, OrchestrationTask, PlatformAdmin


def invitation_settings(context: RequestContext, **overrides: str):
    values = {
        "TELEGRAM_BOT_TOKEN": "bot-token",
        "TELEGRAM_BOT_USERNAME": "test_agent_bot",
        "PLATFORM_ADMIN_USER_IDS": str(context.identity.user_id),
        "INVITATION_POLICY": "admin_only",
        "INVITE_TTL_HOURS": "24",
    }
    values.update(overrides)
    return conversation_settings(**values)


@pytest.mark.asyncio
async def test_admin_creates_hashed_single_use_invite(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = invitation_settings(context)

    result = await create_telegram_invite(session, context, settings)
    invite = await session.get(ChannelInvite, result.invite_id)

    assert invite is not None
    payload = parse_qs(urlparse(result.deep_link).query)["start"][0]
    token = payload.removeprefix("invite_")
    assert invite.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in invite.token_hash
    assert await session.get(PlatformAdmin, context.identity.user_id) is not None
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_non_admin_cannot_create_invite_under_admin_policy(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = invitation_settings(context, PLATFORM_ADMIN_USER_IDS="")

    with pytest.raises(ForbiddenError):
        await create_telegram_invite(session, context, settings)

    assert await session.scalar(select(func.count(ChannelInvite.id))) == 0


@pytest.mark.asyncio
async def test_admin_can_list_and_revoke_pending_invite(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = invitation_settings(context)
    created = await create_telegram_invite(session, context, settings)

    listed = await list_telegram_invites(session, context, settings=settings)
    revoked = await revoke_telegram_invite(
        session,
        context,
        created.invite_id,
        settings,
    )

    assert [item.invite_id for item in listed] == [created.invite_id]
    assert revoked.status == "revoked"
    with pytest.raises(ForbiddenError):
        await revoke_telegram_invite(session, context, created.invite_id, settings)


@pytest.mark.asyncio
async def test_unknown_invite_is_not_visible_or_revocable(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = invitation_settings(context)
    with pytest.raises(NotFoundError):
        await revoke_telegram_invite(session, context, uuid.uuid4(), settings)


@pytest.mark.asyncio
async def test_deterministic_invite_command_does_not_need_model(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = invitation_settings(context)

    response = await handle_account_command(
        session,
        context,
        "/convidar",
        settings,
    )

    assert response is not None
    assert "https://t.me/test_agent_bot?start=invite_" in response
    assert await session.scalar(select(func.count(ChannelInvite.id))) == 1


@pytest.mark.asyncio
async def test_orchestrated_invite_keeps_token_out_of_model_tool_result(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = invitation_settings(context)
    conversation, inbound, run = await conversation_records(
        session,
        context,
        content="Convide uma pessoa para usar o bot.",
    )
    task = OrchestrationTask(
        workspace_id=context.workspace_id,
        user_id=context.identity.user_id,
        conversation_id=conversation.id,
        inbound_message_id=inbound.id,
        intent="invite_management",
        request_text=inbound.content,
        summary="Criar um convite para uma nova conta pessoal.",
        routing_context={},
        allowed_capabilities=["invite_management"],
        status="running",
        idempotency_key=f"invite-test:{inbound.id}",
    )
    session.add(task)
    await session.commit()

    outcome = await ToolRegistry().execute(
        session=session,
        request_context=context,
        conversation=conversation,
        inbound_message=inbound,
        agent_run=run,
        call_id="create-invite",
        tool_name="create_user_invite",
        raw_arguments="{}",
        settings=settings,
        orchestration_task=task,
    )

    await session.refresh(task)
    model_visible_result = outcome.envelope.model_dump_json()
    assert outcome.envelope.code == "invite_created"
    assert "t.me" not in model_visible_result
    assert "start=invite_" not in model_visible_result
    assert "https://t.me/test_agent_bot?start=invite_" in task.routing_context[
        "secure_result_text"
    ]
