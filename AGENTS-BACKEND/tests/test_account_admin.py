from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_settings

from agents_backend.accounts.admin_service import list_accounts
from agents_backend.auth import Identity, RequestContext, resolve_workspace
from agents_backend.conversation.telegram_commands import handle_account_command
from agents_backend.errors import ForbiddenError, UnauthorizedError
from agents_backend.models import (
    AppUser,
    AuditEvent,
    ChannelAccount,
    UserIdentity,
    Workspace,
)


def admin_settings(context: RequestContext, *, admin: bool = True):
    return conversation_settings(
        PLATFORM_ADMIN_USER_IDS=str(context.identity.user_id) if admin else ""
    )


async def target_account(
    session: AsyncSession,
) -> tuple[AppUser, ChannelAccount, ChannelAccount]:
    user = AppUser(id=uuid.uuid4(), display_name="Marina", status="active")
    session.add(user)
    await session.flush()
    session.add(Workspace(owner_user_id=user.id))
    active_channel = ChannelAccount(
        workspace_id=(
            await session.scalar(select(Workspace.id).where(Workspace.owner_user_id == user.id))
        ),
        user_id=user.id,
        provider="telegram",
        external_account_id=f"telegram:{uuid.uuid4()}",
        display_name="Marina",
        verified_at=datetime.now(UTC),
        active=True,
    )
    inactive_channel = ChannelAccount(
        workspace_id=active_channel.workspace_id,
        user_id=user.id,
        provider="whatsapp",
        external_account_id=f"whatsapp:{uuid.uuid4()}",
        display_name="Marina",
        verified_at=datetime.now(UTC),
        active=False,
    )
    session.add_all([active_channel, inactive_channel])
    await session.commit()
    return user, active_channel, inactive_channel


@pytest.mark.asyncio
async def test_only_admin_can_list_accounts(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    user, active_channel, _ = await target_account(session)
    accounts = await list_accounts(session, context, admin_settings(context))

    assert len(accounts) == 2
    assert any(account.platform_admin for account in accounts)
    with pytest.raises(ForbiddenError):
        await list_accounts(
            session,
            RequestContext(
                identity=Identity(user_id=user.id),
                workspace_id=active_channel.workspace_id,
            ),
            admin_settings(context, admin=False),
        )


@pytest.mark.asyncio
async def test_account_commands_confirm_disable_and_reactivate(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    user, active_channel, inactive_channel = await target_account(session)
    settings = admin_settings(context)

    listed = await handle_account_command(
        session, context, "/contas", settings, provider="telegram"
    )
    confirmation = await handle_account_command(
        session,
        context,
        f"/desativarconta {user.id}",
        settings,
        provider="telegram",
    )
    await session.refresh(user)
    assert listed is not None and "Marina — ativa" in listed
    assert confirmation is not None and f"/desativarconta {user.id} confirmar" in confirmation
    assert user.status == "active"

    disabled = await handle_account_command(
        session,
        context,
        f"/desativarconta {user.id} confirmar",
        settings,
        provider="telegram",
    )
    await session.commit()
    await session.refresh(user)
    await session.refresh(active_channel)
    await session.refresh(inactive_channel)
    assert disabled is not None and "Conta desativada" in disabled
    assert user.status == "disabled"
    assert active_channel.active is False
    assert inactive_channel.active is False

    reactivated = await handle_account_command(
        session,
        context,
        f"/reativarconta {user.id}",
        settings,
        provider="telegram",
    )
    await session.commit()
    await session.refresh(user)
    await session.refresh(active_channel)
    await session.refresh(inactive_channel)
    assert reactivated is not None and "Conta reativada" in reactivated
    assert user.status == "active"
    assert active_channel.active is True
    assert inactive_channel.active is False
    assert (
        await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.operation.in_(
                    ["account_disabled_by_admin", "account_reactivated_by_admin"]
                )
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_admin_cannot_disable_self(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    response = await handle_account_command(
        session,
        context,
        f"/desativarconta {context.identity.user_id}",
        admin_settings(context),
        provider="telegram",
    )

    assert response == "Você não pode desativar sua própria conta."


@pytest.mark.asyncio
async def test_non_admin_command_cannot_manage_accounts(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    user, active_channel, _ = await target_account(session)
    non_admin_context = RequestContext(
        identity=Identity(user_id=user.id),
        workspace_id=active_channel.workspace_id,
    )

    response = await handle_account_command(
        session,
        non_admin_context,
        "/contas",
        admin_settings(context, admin=False),
        provider="telegram",
    )

    assert response == "Somente o administrador da plataforma pode gerenciar contas."


@pytest.mark.asyncio
async def test_disabled_account_cannot_resolve_api_workspace(
    session: AsyncSession,
) -> None:
    external_id = uuid.uuid4()
    user = AppUser(id=uuid.uuid4(), status="disabled")
    session.add(user)
    await session.flush()
    session.add_all(
        [
            Workspace(owner_user_id=user.id),
            UserIdentity(
                user_id=user.id,
                provider="supabase",
                provider_subject=str(external_id),
                identity_metadata={},
                verified_at=datetime.now(UTC),
            ),
        ]
    )
    await session.commit()

    with pytest.raises(UnauthorizedError, match="desativada"):
        await resolve_workspace(session, Identity(user_id=external_id))
