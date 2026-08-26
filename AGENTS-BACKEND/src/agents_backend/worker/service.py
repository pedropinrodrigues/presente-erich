from __future__ import annotations

import logging
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.logging import job_id_context
from agents_backend.memory.service import consolidate_extraction
from agents_backend.model_gateway.client import GatewayResult, ModelGateway
from agents_backend.models import Evidence, Fact, Job, ModelRun, Source
from agents_backend.schemas import ExtractionResult

logger = logging.getLogger(__name__)


async def claim_job(session: AsyncSession, worker_id: str) -> Job | None:
    now = datetime.now(UTC)
    statement = (
        select(Job)
        .where(
            or_(
                (Job.status.in_(["queued", "retrying"])) & (Job.available_at <= now),
                (Job.status == "running") & (Job.lease_expires_at < now),
            )
        )
        .order_by(Job.available_at, Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = await session.scalar(statement)
    if job is None:
        await session.rollback()
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_by = worker_id
    job.lease_expires_at = now + timedelta(minutes=5)
    job.error_code = None
    job.error_detail = None
    await session.commit()
    return job


def _model_run(
    job: Job, result: GatewayResult, *, success: bool, error: str | None = None
) -> ModelRun:
    return ModelRun(
        workspace_id=job.workspace_id,
        source_id=job.source_id,
        purpose="extraction",
        model=result.model,
        prompt_version=result.prompt_version,
        schema_version=result.schema_version,
        provider_request_id=result.provider_request_id,
        success=success,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        duration_ms=result.duration_ms,
        error_code=error,
    )


async def process_job(session: AsyncSession, job: Job, gateway: ModelGateway) -> None:
    token = job_id_context.set(str(job.id))
    try:
        source = await session.scalar(
            select(Source).where(
                Source.id == job.source_id,
                Source.workspace_id == job.workspace_id,
            )
        )
        if source is None or source.status == "deleted" or source.transcript is None:
            job.status = "failed"
            job.error_code = "source_unavailable"
            job.error_detail = None
            await session.commit()
            return
        source.status = "processing"
        await session.commit()
        result = await gateway.extract(
            source.transcript,
            source.captured_at.isoformat(),
            source_type=source.source_type,
        )
        if not isinstance(result.value, ExtractionResult):
            raise TypeError("Saída de extração inesperada")
        await consolidate_extraction(session, source, result.value)
        session.add(_model_run(job, result, success=True))
        await session.flush()

        fact_ids = (
            await session.scalars(
                select(Evidence.target_id).where(
                    Evidence.workspace_id == job.workspace_id,
                    Evidence.source_id == source.id,
                    Evidence.target_type == "fact",
                )
            )
        ).all()
        if fact_ids:
            facts = (
                await session.scalars(
                    select(Fact).where(
                        Fact.workspace_id == job.workspace_id,
                        Fact.id.in_(fact_ids),
                        Fact.status.in_(["current", "proposed"]),
                        Fact.embedding.is_(None),
                    )
                )
            ).all()
            for fact in facts:
                try:
                    fact.embedding = await gateway.embed(fact.value_text)
                except Exception:
                    logger.warning("embedding_failed", extra={"fact_id": str(fact.id)})

        source.status = "processed"
        source.error_code = None
        job.status = "completed"
        job.locked_by = None
        job.lease_expires_at = None
        await session.commit()
    except Exception as exc:
        await session.rollback()
        fresh_job = await session.get(Job, job.id)
        source = await session.get(Source, job.source_id) if job.source_id else None
        if fresh_job is None:
            raise
        retry = fresh_job.attempts < fresh_job.max_attempts
        fresh_job.status = "retrying" if retry else "failed"
        fresh_job.available_at = datetime.now(UTC) + timedelta(seconds=2**fresh_job.attempts)
        fresh_job.locked_by = None
        fresh_job.lease_expires_at = None
        fresh_job.error_code = type(exc).__name__
        fresh_job.error_detail = "Falha segura no processamento; consulte os logs correlacionados."
        if source is not None:
            source.status = "received" if retry else "failed"
            source.error_code = type(exc).__name__
        await session.commit()
        logger.exception("job_processing_failed")
    finally:
        job_id_context.reset(token)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
