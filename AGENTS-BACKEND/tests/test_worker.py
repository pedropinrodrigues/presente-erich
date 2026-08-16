from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.models import Job, Source
from agents_backend.worker.service import process_job


class FailingGateway:
    async def extract(self, transcript: str, captured_at: str) -> None:
        raise TimeoutError("synthetic transient failure")


@pytest.mark.asyncio
async def test_worker_retries_and_then_fails_safely(
    session: AsyncSession, context: RequestContext
) -> None:
    source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.now(UTC),
        transcript="Transcrição sintética.",
        transcript_hash="d" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add(source)
    await session.flush()
    job = Job(
        workspace_id=context.workspace_id,
        source_id=source.id,
        job_type="extract_source",
        idempotency_key=f"test:{source.id}",
        pipeline_version="test-v1",
        status="running",
        attempts=1,
        max_attempts=2,
    )
    session.add(job)
    await session.commit()

    await process_job(session, job, FailingGateway())  # type: ignore[arg-type]
    await session.refresh(job)
    await session.refresh(source)
    assert job.status == "retrying"
    assert source.status == "received"
    assert job.error_detail and "Transcrição" not in job.error_detail

    job.status = "running"
    job.attempts = 2
    await session.commit()
    await process_job(session, job, FailingGateway())  # type: ignore[arg-type]
    await session.refresh(job)
    await session.refresh(source)
    assert job.status == "failed"
    assert source.status == "failed"
