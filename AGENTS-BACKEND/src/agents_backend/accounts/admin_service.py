from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings
from agents_backend.errors import ConflictError, ForbiddenError, NotFoundError
from agents_backend.invitations.service import is_platform_admin
from agents_backend.models import (
    AppUser,
    AuditEvent,
    ChannelAccount,
    PlatformAdmin,
    Workspace,
)


@dataclass(frozen=True, slots=True)
class ManagedAccount:
    user_id: uuid.UUID
    display_name: str | None
    status: str
    platform_admin: bool


async def _require_admin(
    session: AsyncSession,
    context: RequestContext,
    settings: Settings,
) -> None:
    if not await is_platform_admin(session, context.identity.user_id, settings):
        raise ForbiddenError("Somente o administrador da plataforma pode gerenciar contas.")


async def list_accounts(
    session: AsyncSession,
    context: RequestContext,
    settings: Settings,
    *,
    limit: int = 100,
) -> list[ManagedAccount]:
    await _require_admin(session, context, settings)
    users = list(
        (
            await session.scalars(
                select(AppUser).order_by(AppUser.created_at, AppUser.id).limit(limit)
            )
        ).all()
    )
    database_admins = set(
        (
            await session.scalars(
                select(PlatformAdmin.user_id).where(PlatformAdmin.status == "active")
            )
        ).all()
    )
    admin_ids = database_admins | set(settings.configured_platform_admin_ids)
    return [
        ManagedAccount(
            user_id=user.id,
            display_name=user.display_name,
            status=user.status,
            platform_admin=user.id in admin_ids,
        )
        for user in users
    ]


async def get_managed_account(
    session: AsyncSession,
    context: RequestContext,
    target_user_id: uuid.UUID,
    settings: Settings,
) -> ManagedAccount:
    await _require_admin(session, context, settings)
    user = await session.get(AppUser, target_user_id)
    if user is None or user.status == "deleted":
        raise NotFoundError("Conta não encontrada.")
    return ManagedAccount(
        user_id=user.id,
        display_name=user.display_name,
        status=user.status,
        platform_admin=await is_platform_admin(session, user.id, settings),
    )


async def disable_account(
    session: AsyncSession,
    context: RequestContext,
    target_user_id: uuid.UUID,
    settings: Settings,
) -> ManagedAccount:
    await _require_admin(session, context, settings)
    if target_user_id == context.identity.user_id:
        raise ForbiddenError("Você não pode desativar sua própria conta.")
    user = await session.get(AppUser, target_user_id, with_for_update=True)
    if user is None or user.status == "deleted":
        raise NotFoundError("Conta não encontrada.")
    if await is_platform_admin(session, user.id, settings):
        raise ForbiddenError("Outra conta administradora não pode ser desativada por este comando.")
    if user.status == "disabled":
        raise ConflictError("account_already_disabled", "A conta já está desativada.")
    if user.status != "active":
        raise ConflictError("account_not_active", "A conta não está ativa.")

    channels = list(
        (
            await session.scalars(
                select(ChannelAccount)
                .where(
                    ChannelAccount.user_id == user.id,
                    ChannelAccount.active.is_(True),
                )
                .with_for_update()
            )
        ).all()
    )
    channel_ids = [str(channel.id) for channel in channels]
    for channel in channels:
        channel.active = False
    user.status = "disabled"

    workspace_id = await session.scalar(
        select(Workspace.id).where(Workspace.owner_user_id == user.id)
    )
    if workspace_id is None:
        raise NotFoundError("Workspace da conta não encontrado.")
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=context.identity.user_id,
            operation="account_disabled_by_admin",
            target_type="app_user",
            target_id=user.id,
            event_metadata={"deactivated_channel_account_ids": channel_ids},
        )
    )
    await session.flush()
    return ManagedAccount(user.id, user.display_name, user.status, False)


async def reactivate_account(
    session: AsyncSession,
    context: RequestContext,
    target_user_id: uuid.UUID,
    settings: Settings,
) -> ManagedAccount:
    await _require_admin(session, context, settings)
    user = await session.get(AppUser, target_user_id, with_for_update=True)
    if user is None or user.status == "deleted":
        raise NotFoundError("Conta não encontrada.")
    if user.status == "active":
        raise ConflictError("account_already_active", "A conta já está ativa.")
    if user.status != "disabled":
        raise ConflictError("account_not_disabled", "A conta não está desativada.")

    last_disable = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.operation == "account_disabled_by_admin",
            AuditEvent.target_type == "app_user",
            AuditEvent.target_id == user.id,
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    raw_channel_ids = (
        last_disable.event_metadata.get("deactivated_channel_account_ids", [])
        if last_disable is not None
        else []
    )
    channel_ids: list[uuid.UUID] = []
    if isinstance(raw_channel_ids, list):
        for value in raw_channel_ids:
            try:
                channel_ids.append(uuid.UUID(str(value)))
            except ValueError:
                continue
    reactivated_channel_ids: list[uuid.UUID] = []
    if channel_ids:
        channels = list(
            (
                await session.scalars(
                    select(ChannelAccount)
                    .where(
                        ChannelAccount.id.in_(channel_ids),
                        ChannelAccount.user_id == user.id,
                        ChannelAccount.verified_at.is_not(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for channel in channels:
            channel.active = True
            reactivated_channel_ids.append(channel.id)

    user.status = "active"
    workspace_id = await session.scalar(
        select(Workspace.id).where(Workspace.owner_user_id == user.id)
    )
    if workspace_id is None:
        raise NotFoundError("Workspace da conta não encontrado.")
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=context.identity.user_id,
            operation="account_reactivated_by_admin",
            target_type="app_user",
            target_id=user.id,
            event_metadata={
                "reactivated_channel_account_ids": [str(value) for value in reactivated_channel_ids]
            },
        )
    )
    await session.flush()
    return ManagedAccount(
        user.id,
        user.display_name,
        user.status,
        await is_platform_admin(session, user.id, settings),
    )
