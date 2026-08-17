from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext, authenticate_token, resolve_workspace
from agents_backend.conversation.service import ConversationService
from agents_backend.db import get_session
from agents_backend.errors import UnauthorizedError

SessionDependency = Annotated[AsyncSession, Depends(get_session)]


async def get_request_context(
    session: SessionDependency,
    authorization: Annotated[str | None, Header()] = None,
) -> RequestContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError()
    identity = await authenticate_token(authorization.removeprefix("Bearer ").strip())
    return await resolve_workspace(session, identity)


ContextDependency = Annotated[RequestContext, Depends(get_request_context)]


@lru_cache
def get_conversation_service() -> ConversationService:
    return ConversationService()


ConversationServiceDependency = Annotated[ConversationService, Depends(get_conversation_service)]
