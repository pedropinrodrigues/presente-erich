from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import get_settings
from agents_backend.errors import ConflictError, NotFoundError
from agents_backend.models import AuditEvent, Job, Source, SourceStatus
from agents_backend.schemas import IngestTranscriptResponse, SourceResponse, TranscriptEvent

PIPELINE_VERSION = "extraction-v1"


def transcript_payload_hash(event: TranscriptEvent) -> str:
    payload = {
        "capture_id": str(event.capture_id),
        "source": event.source.strip().lower(),
        "captured_at": event.captured_at.isoformat(),
        "transcript": event.transcript,
        "duration_seconds": event.duration_seconds,
        "language": event.language,
        "metadata": event.metadata,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def ingest_transcript(
    session: AsyncSession,
    context: RequestContext,
    event: TranscriptEvent,
    *,
    commit: bool = True,
) -> tuple[IngestTranscriptResponse, int]:
    payload_hash = transcript_payload_hash(event)
    existing = await session.scalar(
        select(Source).where(
            Source.workspace_id == context.workspace_id,
            Source.capture_id == event.capture_id,
        )
    )
    if existing:
        if existing.transcript_hash != payload_hash:
            raise ConflictError(
                "capture_id_conflict",
                "O capture_id já existe com conteúdo incompatível.",
            )
        return (
            IngestTranscriptResponse(
                source_id=existing.id,
                status=existing.status,
                idempotent_replay=True,
            ),
            200,
        )

    source = Source(
        workspace_id=context.workspace_id,
        capture_id=event.capture_id,
        source_type=event.source.strip().lower(),
        captured_at=event.captured_at,
        transcript=event.transcript,
        transcript_hash=payload_hash,
        duration_seconds=event.duration_seconds,
        language=event.language,
        source_metadata=event.metadata,
        status=SourceStatus.RECEIVED.value,
    )
    session.add(source)
    await session.flush()
    session.add(
        Job(
            workspace_id=context.workspace_id,
            source_id=source.id,
            job_type="extract_source",
            idempotency_key=f"extract:{source.id}:{PIPELINE_VERSION}",
            pipeline_version=PIPELINE_VERSION,
            max_attempts=get_settings().worker_max_attempts,
        )
    )
    session.add(
        AuditEvent(
            workspace_id=context.workspace_id,
            actor_user_id=context.identity.user_id,
            operation="source_ingested",
            target_type="source",
            target_id=source.id,
            event_metadata={"capture_id": str(event.capture_id)},
        )
    )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return (
        IngestTranscriptResponse(
            source_id=source.id,
            status=source.status,
            idempotent_replay=False,
        ),
        201,
    )


async def get_source(
    session: AsyncSession, context: RequestContext, source_id: uuid.UUID
) -> SourceResponse:
    source = await session.scalar(
        select(Source).where(
            Source.id == source_id,
            Source.workspace_id == context.workspace_id,
            Source.status != SourceStatus.DELETED.value,
        )
    )
    if source is None or source.transcript is None:
        raise NotFoundError()
    return SourceResponse(
        id=source.id,
        capture_id=source.capture_id,
        source=source.source_type,
        captured_at=source.captured_at,
        transcript=source.transcript,
        duration_seconds=source.duration_seconds,
        language=source.language,
        metadata=source.source_metadata,
        status=source.status,
        created_at=source.created_at,
    )
