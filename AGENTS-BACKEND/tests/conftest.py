from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents_backend.auth import Identity, RequestContext
from agents_backend.models import AppUser, Base, Workspace


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


@pytest_asyncio.fixture
async def context(session: AsyncSession) -> RequestContext:
    user_id = uuid.uuid4()
    session.add(AppUser(id=user_id, status="active"))
    workspace = Workspace(owner_user_id=user_id)
    session.add(workspace)
    await session.commit()
    return RequestContext(identity=Identity(user_id=user_id), workspace_id=workspace.id)


@pytest.fixture
def transcript_payload() -> dict[str, object]:
    return {
        "capture_id": str(uuid.uuid4()),
        "source": "iphone",
        "captured_at": "2026-08-15T10:00:00-03:00",
        "transcript": "Marina decidiu lançar o Projeto Atlas em setembro.",
        "duration_seconds": 30,
        "language": "pt-BR",
        "metadata": {"synthetic": True},
    }
