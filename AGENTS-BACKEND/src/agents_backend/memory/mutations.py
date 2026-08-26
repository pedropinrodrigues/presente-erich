from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.errors import AppError, NotFoundError
from agents_backend.memory.service import fingerprint
from agents_backend.models import AuditEvent, Commitment, Entity, Evidence, Fact, Job, Source
from agents_backend.schemas import CorrectionRequest, MutationResponse


async def correct_memory(
    session: AsyncSession,
    context: RequestContext,
    request: CorrectionRequest,
    *,
    commit: bool = True,
) -> MutationResponse:
    target: Fact | Commitment | None
    if request.target_type == "fact":
        target = await session.scalar(
            select(Fact).where(
                Fact.id == request.target_id,
                Fact.workspace_id == context.workspace_id,
                Fact.status != "deleted",
            )
        )
    else:
        target = await session.scalar(
            select(Commitment).where(
                Commitment.id == request.target_id,
                Commitment.workspace_id == context.workspace_id,
                Commitment.status != "deleted",
            )
        )
    if target is None:
        raise NotFoundError()
    if request.operation == "replace" and request.value is None:
        raise AppError("replacement_value_required", "replace exige value.", 400)

    resulting_target_id = target.id
    if request.operation == "replace":
        if isinstance(target, Fact):
            target.status = "superseded"
            replacement_value: dict[str, Any] = (
                request.value if isinstance(request.value, dict) else {"value": request.value}
            )
            value_text = str(replacement_value.get("value", request.value))
            replacement = Fact(
                workspace_id=context.workspace_id,
                subject_entity_id=target.subject_entity_id,
                predicate=target.predicate,
                value=replacement_value,
                value_text=value_text,
                fact_type=target.fact_type,
                fingerprint=fingerprint(target.id, replacement_value, datetime.now(UTC)),
                status="current",
                confidence=1.0,
                valid_from=datetime.now(UTC),
                supersedes_id=target.id,
            )
            session.add(replacement)
            await session.flush()
            resulting_target_id = replacement.id
        else:
            target.description = str(request.value)
            target.fingerprint = fingerprint(target.id, request.value, datetime.now(UTC))
            target.confidence = 1.0
    elif request.operation == "dispute":
        if isinstance(target, Fact):
            target.status = "disputed"
        else:
            target.status = "cancelled"
    else:
        target.status = "deleted"
        target.deleted_at = datetime.now(UTC)
        if isinstance(target, Fact):
            target.embedding = None
        await session.execute(
            delete(Evidence).where(
                Evidence.workspace_id == context.workspace_id,
                Evidence.target_type == request.target_type,
                Evidence.target_id == target.id,
            )
        )

    audit = AuditEvent(
        workspace_id=context.workspace_id,
        actor_user_id=context.identity.user_id,
        operation=f"memory_{request.operation}",
        target_type=request.target_type,
        target_id=resulting_target_id,
        reason=request.reason,
        event_metadata={"previous_target_id": str(target.id)},
    )
    session.add(audit)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return MutationResponse(
        target_id=resulting_target_id,
        status=(
            "current"
            if request.operation == "replace"
            else "deleted"
            if request.operation == "delete"
            else "disputed"
        ),
        audit_event_id=audit.id,
    )


async def delete_memory_target(
    session: AsyncSession,
    context: RequestContext,
    target_id: uuid.UUID,
    reason: str | None,
    *,
    commit: bool = True,
) -> MutationResponse:
    fact = await session.scalar(
        select(Fact).where(Fact.id == target_id, Fact.workspace_id == context.workspace_id)
    )
    target_type = "fact"
    target: Fact | Commitment | None = fact
    if target is None:
        target = await session.scalar(
            select(Commitment).where(
                Commitment.id == target_id, Commitment.workspace_id == context.workspace_id
            )
        )
        target_type = "commitment"
    if target is None:
        raise NotFoundError()
    request = CorrectionRequest(
        target_id=target_id,
        target_type=target_type,
        operation="delete",
        reason=reason,
    )
    return await correct_memory(session, context, request, commit=commit)


async def delete_source(
    session: AsyncSession,
    context: RequestContext,
    source_id: uuid.UUID,
    reason: str | None,
    *,
    commit: bool = True,
) -> MutationResponse:
    source = await session.scalar(
        select(Source).where(
            Source.id == source_id,
            Source.workspace_id == context.workspace_id,
            Source.status != "deleted",
        )
    )
    if source is None:
        raise NotFoundError()
    linked = (
        await session.execute(
            select(Evidence.target_type, Evidence.target_id).where(
                Evidence.workspace_id == context.workspace_id,
                Evidence.source_id == source.id,
            )
        )
    ).all()
    await session.execute(
        delete(Evidence).where(
            Evidence.workspace_id == context.workspace_id, Evidence.source_id == source.id
        )
    )
    fact_ids = {target_id for target_type, target_id in linked if target_type == "fact"}
    commitment_ids = {
        target_id for target_type, target_id in linked if target_type == "commitment"
    }
    entity_ids = {target_id for target_type, target_id in linked if target_type == "entity"}
    facts = list(
        (
            await session.scalars(
                select(Fact)
                .where(Fact.id.in_(fact_ids))
                .order_by(Fact.created_at.desc())
            )
        ).all()
    )
    for fact in facts:
        remaining = await session.scalar(
            select(func.count(Evidence.id)).where(
                Evidence.workspace_id == context.workspace_id,
                Evidence.target_type == "fact",
                Evidence.target_id == fact.id,
            )
        )
        if remaining == 0:
            was_current = fact.status == "current"
            fact.status = "deleted"
            fact.deleted_at = datetime.now(UTC)
            fact.embedding = None
            if was_current and fact.supersedes_id is not None:
                previous = await session.get(Fact, fact.supersedes_id)
                competing = await session.scalar(
                    select(func.count(Fact.id)).where(
                        Fact.workspace_id == fact.workspace_id,
                        Fact.subject_entity_id == fact.subject_entity_id,
                        Fact.predicate == fact.predicate,
                        Fact.status == "current",
                        Fact.id != fact.id,
                    )
                )
                if previous is not None and previous.status == "superseded" and not competing:
                    previous.status = "current"
    commitments = list(
        (
            await session.scalars(select(Commitment).where(Commitment.id.in_(commitment_ids)))
        ).all()
    )
    for commitment in commitments:
        remaining = await session.scalar(
            select(func.count(Evidence.id)).where(
                Evidence.workspace_id == context.workspace_id,
                Evidence.target_type == "commitment",
                Evidence.target_id == commitment.id,
            )
        )
        if remaining == 0:
            commitment.status = "deleted"
            commitment.deleted_at = datetime.now(UTC)
    entities = list(
        (await session.scalars(select(Entity).where(Entity.id.in_(entity_ids)))).all()
    )
    for entity in entities:
        remaining_evidence = await session.scalar(
            select(func.count(Evidence.id)).where(
                Evidence.workspace_id == context.workspace_id,
                Evidence.target_type == "entity",
                Evidence.target_id == entity.id,
            )
        )
        active_facts = await session.scalar(
            select(func.count(Fact.id)).where(
                Fact.workspace_id == context.workspace_id,
                Fact.subject_entity_id == entity.id,
                Fact.status != "deleted",
            )
        )
        active_commitments = await session.scalar(
            select(func.count(Commitment.id)).where(
                Commitment.workspace_id == context.workspace_id,
                Commitment.responsible_entity_id == entity.id,
                Commitment.status != "deleted",
            )
        )
        if not remaining_evidence and not active_facts and not active_commitments:
            entity.status = "deleted"
    jobs = (
        await session.scalars(
            select(Job).where(
                Job.workspace_id == context.workspace_id,
                Job.source_id == source.id,
                Job.status.in_(["queued", "retrying", "running"]),
            )
        )
    ).all()
    for job in jobs:
        job.status = "failed"
        job.error_code = "source_deleted"
        job.error_detail = None
    source.status = "deleted"
    source.transcript = None
    source.source_metadata = {}
    source.deleted_at = datetime.now(UTC)
    audit = AuditEvent(
        workspace_id=context.workspace_id,
        actor_user_id=context.identity.user_id,
        operation="source_deleted",
        target_type="source",
        target_id=source.id,
        reason=reason,
        event_metadata={},
    )
    session.add(audit)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return MutationResponse(target_id=source.id, status="deleted", audit_event_id=audit.id)
