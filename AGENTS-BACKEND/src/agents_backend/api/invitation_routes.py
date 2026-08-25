from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from agents_backend.api.dependencies import ContextDependency, SessionDependency
from agents_backend.invitations.schemas import (
    AccountResponse,
    TelegramInviteListItem,
    TelegramInviteResponse,
)
from agents_backend.invitations.service import (
    create_telegram_invite,
    get_my_account,
    list_telegram_invites,
    revoke_telegram_invite,
)

router = APIRouter(prefix="/v1")


@router.post("/invites/telegram", response_model=TelegramInviteResponse, status_code=201)
async def post_telegram_invite(
    session: SessionDependency,
    context: ContextDependency,
) -> TelegramInviteResponse:
    result = await create_telegram_invite(session, context)
    await session.commit()
    return result


@router.get("/invites/telegram", response_model=list[TelegramInviteListItem])
async def get_telegram_invites(
    session: SessionDependency,
    context: ContextDependency,
    all_users: bool = Query(default=False),
) -> list[TelegramInviteListItem]:
    result = await list_telegram_invites(
        session,
        context,
        include_all=all_users,
    )
    await session.commit()
    return result


@router.delete(
    "/invites/telegram/{invite_id}", response_model=TelegramInviteListItem
)
async def delete_telegram_invite(
    invite_id: uuid.UUID,
    session: SessionDependency,
    context: ContextDependency,
) -> TelegramInviteListItem:
    result = await revoke_telegram_invite(session, context, invite_id)
    await session.commit()
    return result


@router.get("/account", response_model=AccountResponse)
async def get_account(
    session: SessionDependency,
    context: ContextDependency,
) -> AccountResponse:
    result = await get_my_account(session, context)
    await session.commit()
    return result
