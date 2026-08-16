from __future__ import annotations

import uuid
from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import Identity, RequestContext
from agents_backend.errors import ConflictError, NotFoundError
from agents_backend.ingestion.service import get_source, ingest_transcript
from agents_backend.models import Job, Source, Workspace
from agents_backend.schemas import TranscriptEvent


@pytest.mark.asyncio
async def test_ingestion_is_idempotent(
    session: AsyncSession,
    context: RequestContext,
    transcript_payload: dict[str, object],
) -> None:
    event = TranscriptEvent.model_validate(transcript_payload)
    first, first_status = await ingest_transcript(session, context, event)
    replay, replay_status = await ingest_transcript(session, context, event)

    assert first_status == 201
    assert replay_status == 200
    assert replay.source_id == first.source_id
    assert replay.idempotent_replay is True
    assert await session.scalar(select(func.count(Source.id))) == 1
    assert await session.scalar(select(func.count(Job.id))) == 1


@pytest.mark.asyncio
async def test_capture_id_conflict_is_rejected(
    session: AsyncSession,
    context: RequestContext,
    transcript_payload: dict[str, object],
) -> None:
    await ingest_transcript(session, context, TranscriptEvent.model_validate(transcript_payload))
    incompatible = deepcopy(transcript_payload)
    incompatible["transcript"] = "Conteúdo incompatível."
    with pytest.raises(ConflictError) as error:
        await ingest_transcript(session, context, TranscriptEvent.model_validate(incompatible))
    assert error.value.code == "capture_id_conflict"


def test_transcript_requires_timezone(transcript_payload: dict[str, object]) -> None:
    transcript_payload["captured_at"] = "2026-08-15T10:00:00"
    with pytest.raises(ValueError):
        TranscriptEvent.model_validate(transcript_payload)


@pytest.mark.asyncio
async def test_source_is_isolated_by_workspace(
    session: AsyncSession,
    context: RequestContext,
    transcript_payload: dict[str, object],
) -> None:
    created, _ = await ingest_transcript(
        session, context, TranscriptEvent.model_validate(transcript_payload)
    )
    other_user_id = uuid.uuid4()
    other_workspace = Workspace(owner_user_id=other_user_id)
    session.add(other_workspace)
    await session.commit()
    other_context = RequestContext(
        identity=Identity(user_id=other_user_id), workspace_id=other_workspace.id
    )
    with pytest.raises(NotFoundError):
        await get_source(session, other_context, created.source_id)
