from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings, get_settings
from agents_backend.errors import ForbiddenError, NotFoundError
from agents_backend.models import (
    AppUser,
    AuditEvent,
    ChannelInvite,
    PlatformAdmin,
)

from .schemas import AccountResponse, TelegramInviteListItem, TelegramInviteResponse


async def is_platform_admin(
    session: AsyncSession,
    user_id: uuid.UUID,
    settings: Settings | None = None,
) -> bool:
    selected_settings = settings or get_settings()
    admin = await session.get(PlatformAdmin, user_id)
    if admin is None and user_id in selected_settings.configured_platform_admin_ids:
        admin = PlatformAdmin(user_id=user_id, status="active", permissions=["*"])
        session.add(admin)
        await session.flush()
    return admin is not None and admin.status == "active"


async def _require_invitation_authority(
    session: AsyncSession,
    context: RequestContext,
    settings: Settings,
) -> None:
    user = await session.get(AppUser, context.identity.user_id)
    if user is None or user.status != "active":
        raise ForbiddenError("Sua conta não está ativa.")
    if settings.invitation_policy == "admin_only" and not await is_platform_admin(
        session, context.identity.user_id, settings
    ):
        raise ForbiddenError("Somente o administrador da plataforma pode criar convites.")


def _invite_item(invite: ChannelInvite) -> TelegramInviteListItem:
    return TelegramInviteListItem(
        invite_id=invite.id,
        status=invite.status,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        revoked_at=invite.revoked_at,
        created_at=invite.created_at,
    )


async def _expire_pending_invites(session: AsyncSession) -> None:
    await session.execute(
        update(ChannelInvite)
        .where(
            ChannelInvite.status == "pending",
            ChannelInvite.expires_at <= datetime.now(UTC),
        )
        .values(status="expired")
    )


async def create_telegram_invite(
    session: AsyncSession,
    context: RequestContext,
    settings: Settings | None = None,
) -> TelegramInviteResponse:
    selected_settings = settings or get_settings()
    await _require_invitation_authority(session, context, selected_settings)
    bot_username = selected_settings.telegram_bot_username
    if not bot_username:
        raise RuntimeError("TELEGRAM_BOT_USERNAME não está configurado")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(UTC)
    invite = ChannelInvite(
        created_by_user_id=context.identity.user_id,
        created_by_workspace_id=context.workspace_id,
        token_hash=token_hash,
        purpose="personal_account",
        status="pending",
        expires_at=now + timedelta(hours=selected_settings.invite_ttl_hours),
    )
    session.add(invite)
    await session.flush()
    session.add(
        AuditEvent(
            workspace_id=context.workspace_id,
            actor_user_id=context.identity.user_id,
            operation="invite_created",
            target_type="channel_invite",
            target_id=invite.id,
            event_metadata={"expires_at": invite.expires_at.isoformat()},
        )
    )
    await session.flush()
    payload = f"invite_{token}"
    deep_link = f"https://t.me/{bot_username}?start={payload}"
    share_text = "Use este convite para criar sua conta pessoal no bot."
    share_link = f"https://t.me/share/url?url={quote(deep_link, safe='')}&text={quote(share_text)}"
    return TelegramInviteResponse(
        invite_id=invite.id,
        deep_link=deep_link,
        share_link=share_link,
        expires_at=invite.expires_at,
        status=invite.status,
    )


async def list_telegram_invites(
    session: AsyncSession,
    context: RequestContext,
    *,
    include_all: bool = False,
    settings: Settings | None = None,
) -> list[TelegramInviteListItem]:
    selected_settings = settings or get_settings()
    await _expire_pending_invites(session)
    statement = select(ChannelInvite)
    if include_all:
        if not await is_platform_admin(session, context.identity.user_id, selected_settings):
            raise ForbiddenError()
    else:
        statement = statement.where(
            ChannelInvite.created_by_user_id == context.identity.user_id
        )
    invites = list(
        (await session.scalars(statement.order_by(ChannelInvite.created_at.desc()))).all()
    )
    return [_invite_item(invite) for invite in invites]


async def revoke_telegram_invite(
    session: AsyncSession,
    context: RequestContext,
    invite_id: uuid.UUID,
    settings: Settings | None = None,
) -> TelegramInviteListItem:
    selected_settings = settings or get_settings()
    await _expire_pending_invites(session)
    admin = await is_platform_admin(session, context.identity.user_id, selected_settings)
    statement = select(ChannelInvite).where(ChannelInvite.id == invite_id)
    if not admin:
        statement = statement.where(
            ChannelInvite.created_by_user_id == context.identity.user_id
        )
    invite = await session.scalar(statement.with_for_update())
    if invite is None:
        raise NotFoundError("Convite não encontrado.")
    if invite.status != "pending":
        raise ForbiddenError("Somente convites pendentes podem ser revogados.")
    invite.status = "revoked"
    invite.revoked_at = datetime.now(UTC)
    session.add(
        AuditEvent(
            workspace_id=invite.created_by_workspace_id,
            actor_user_id=context.identity.user_id,
            operation="invite_revoked",
            target_type="channel_invite",
            target_id=invite.id,
            event_metadata={},
        )
    )
    await session.flush()
    return _invite_item(invite)


async def get_my_account(
    session: AsyncSession,
    context: RequestContext,
    settings: Settings | None = None,
) -> AccountResponse:
    selected_settings = settings or get_settings()
    user = await session.get(AppUser, context.identity.user_id)
    if user is None:
        raise NotFoundError("Conta não encontrada.")
    return AccountResponse(
        user_id=user.id,
        workspace_id=context.workspace_id,
        display_name=user.display_name,
        locale=user.locale,
        timezone=user.timezone,
        status=user.status,
        platform_admin=await is_platform_admin(session, user.id, selected_settings),
        invitation_policy=selected_settings.invitation_policy,
    )
