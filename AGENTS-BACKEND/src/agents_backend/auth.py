from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.config import get_settings
from agents_backend.errors import UnauthorizedError
from agents_backend.models import Workspace


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: uuid.UUID
    email: str | None = None


@dataclass(frozen=True, slots=True)
class RequestContext:
    identity: Identity
    workspace_id: uuid.UUID


@lru_cache
def get_jwks_client() -> PyJWKClient:
    return PyJWKClient(get_settings().supabase_jwks_url, cache_keys=True, lifespan=300)


def _decode_token(token: str) -> dict[str, object]:
    settings = get_settings()
    signing_key = get_jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience="authenticated",
        issuer=settings.supabase_issuer,
        options={"require": ["exp", "sub", "aud"]},
    )


async def authenticate_token(token: str) -> Identity:
    try:
        claims = await asyncio.to_thread(_decode_token, token)
        return Identity(user_id=uuid.UUID(str(claims["sub"])), email=claims.get("email"))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("Token ausente, expirado ou inválido.") from exc


async def resolve_workspace(session: AsyncSession, identity: Identity) -> RequestContext:
    statement = (
        insert(Workspace)
        .values(id=uuid.uuid4(), owner_user_id=identity.user_id)
        .on_conflict_do_nothing(index_elements=[Workspace.owner_user_id])
    )
    await session.execute(statement)
    await session.commit()
    workspace_id = await session.scalar(
        select(Workspace.id).where(Workspace.owner_user_id == identity.user_id)
    )
    if workspace_id is None:
        raise RuntimeError("Não foi possível resolver o workspace pessoal")
    return RequestContext(identity=identity, workspace_id=workspace_id)
