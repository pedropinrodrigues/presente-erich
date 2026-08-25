from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.config import Settings
from agents_backend.models import (
    AudioTranscriptionJob,
    ChannelMessage,
    OrchestrationTask,
    OutboxMessage,
    ScheduledRun,
    WorkerHeartbeat,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _queue_snapshot(session: AsyncSession, now: datetime) -> dict[str, Any]:
    definitions = {
        "audio_transcription": (
            AudioTranscriptionJob,
            or_(
                AudioTranscriptionJob.status.in_(["queued", "retrying"]),
                (AudioTranscriptionJob.status == "running")
                & (AudioTranscriptionJob.lease_expires_at < now),
            ),
        ),
        "channel_inbound": (
            ChannelMessage,
            (
                (ChannelMessage.direction == "inbound")
                & (
                    ChannelMessage.status.in_(["received", "retrying"])
                    | (
                        (ChannelMessage.status == "processing")
                        & (ChannelMessage.lease_expires_at < now)
                    )
                )
            ),
        ),
        "outbox": (
            OutboxMessage,
            or_(
                OutboxMessage.status.in_(["pending", "retrying"]),
                (OutboxMessage.status == "sending") & (OutboxMessage.lease_expires_at < now),
            ),
        ),
        "orchestration": (
            OrchestrationTask,
            or_(
                OrchestrationTask.status == "queued",
                (OrchestrationTask.status == "running")
                & (OrchestrationTask.lease_expires_at < now),
            ),
        ),
        "scheduled_runs": (
            ScheduledRun,
            or_(
                ScheduledRun.status.in_(["queued", "retrying"]),
                (ScheduledRun.status == "running") & (ScheduledRun.lease_expires_at < now),
            ),
        ),
    }
    snapshot: dict[str, Any] = {}
    max_lag = 0
    for name, (model, predicate) in definitions.items():
        count, oldest = (
            await session.execute(
                select(func.count(model.id), func.min(model.created_at)).where(predicate)
            )
        ).one()
        lag = max(0, int((now - _as_utc(oldest)).total_seconds())) if oldest else 0
        snapshot[name] = {"count": int(count), "oldest_lag_seconds": lag}
        max_lag = max(max_lag, lag)
    snapshot["max_lag_seconds"] = max_lag
    return snapshot


async def record_worker_heartbeat(
    session: AsyncSession,
    *,
    worker_id: str,
    status: str,
    consecutive_infra_failures: int,
    settings: Settings,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    snapshot = await _queue_snapshot(session, now)
    heartbeat = await session.get(WorkerHeartbeat, worker_id)
    if heartbeat is None:
        heartbeat = WorkerHeartbeat(
            worker_id=worker_id,
            status=status,
            deployment_revision=settings.deployment_revision,
            consecutive_infra_failures=consecutive_infra_failures,
            heartbeat_metadata=snapshot,
            started_at=now,
            last_seen_at=now,
        )
        session.add(heartbeat)
    else:
        heartbeat.status = status
        heartbeat.deployment_revision = settings.deployment_revision
        heartbeat.consecutive_infra_failures = consecutive_infra_failures
        heartbeat.heartbeat_metadata = snapshot
        heartbeat.last_seen_at = now
    await session.commit()
    return snapshot
