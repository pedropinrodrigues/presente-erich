from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.models import (
    AuditEvent,
    Commitment,
    CommitmentStatus,
    Entity,
    Evidence,
    Fact,
    FactStatus,
    Source,
)
from agents_backend.schemas import ExtractionResult


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).split())


def fingerprint(*parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validated_datetime(value: datetime | None, reference: datetime) -> datetime | None:
    """Discard model-produced dates that cannot be safely related to the source."""
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    reference_utc = reference.astimezone(UTC)
    value_utc = value.astimezone(UTC)
    if abs((value_utc - reference_utc).days) > 3660:
        return None
    return value


TOKEN_STOPWORDS = {
    "a",
    "ao",
    "ate",
    "com",
    "da",
    "de",
    "do",
    "e",
    "em",
    "fim",
    "o",
    "para",
    "por",
    "que",
}


def semantic_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_name(value).split()
        if len(token) >= 3 and token not in TOKEN_STOPWORDS
    }


def is_completion_fact(*parts: str) -> bool:
    text = normalize_name(" ".join(parts))
    negative_markers = (
        "ainda nao",
        "nao esta",
        "nao foi",
        "nao conclu",
        "nao complet",
        "pendente",
    )
    if any(marker in text for marker in negative_markers):
        return False
    completion_markers = (
        "agendad",
        "complet",
        "conclu",
        "entreg",
        "enviad",
        "marcad",
        "publicad",
        "pront",
        "recebid",
    )
    return any(marker in text for marker in completion_markers)


async def _matching_completed_commitment(
    session: AsyncSession,
    source: Source,
    description: str,
    excerpt: str,
    responsible_id: uuid.UUID | None,
) -> Commitment | None:
    candidates = list(
        (
            await session.scalars(
                select(Commitment).where(
                    Commitment.workspace_id == source.workspace_id,
                    Commitment.status.in_(
                        [CommitmentStatus.OPEN.value, CommitmentStatus.COMPLETED.value]
                    ),
                )
            )
        ).all()
    )
    if not candidates:
        return None
    completion_tokens = semantic_tokens(f"{description} {excerpt}")
    responsible_matches = [
        candidate
        for candidate in candidates
        if responsible_id is not None and candidate.responsible_entity_id == responsible_id
    ]
    if len(responsible_matches) == 1:
        return responsible_matches[0]
    scored: list[tuple[float, Commitment]] = []
    for candidate in candidates:
        existing_tokens = semantic_tokens(candidate.description)
        overlap = len(existing_tokens & completion_tokens)
        if overlap < 2:
            continue
        score = overlap / max(len(existing_tokens), 1)
        if responsible_id is not None and candidate.responsible_entity_id == responsible_id:
            score += 0.35
        scored.append((score, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < 0.25:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


async def _evidence(
    session: AsyncSession,
    source: Source,
    target_type: str,
    target_id: uuid.UUID,
    excerpt: str,
    start_offset: int | None,
    end_offset: int | None,
) -> None:
    evidence_hash = hashlib.sha256(excerpt.encode()).hexdigest()
    exists = await session.scalar(
        select(Evidence.id).where(
            Evidence.workspace_id == source.workspace_id,
            Evidence.target_type == target_type,
            Evidence.target_id == target_id,
            Evidence.source_id == source.id,
            Evidence.excerpt_hash == evidence_hash,
        )
    )
    if not exists:
        session.add(
            Evidence(
                workspace_id=source.workspace_id,
                source_id=source.id,
                target_type=target_type,
                target_id=target_id,
                excerpt=excerpt,
                excerpt_hash=evidence_hash,
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )


async def consolidate_extraction(
    session: AsyncSession,
    source: Source,
    result: ExtractionResult,
) -> None:
    entity_ids: dict[str, uuid.UUID] = {}
    for candidate in result.entities:
        normalized = normalize_name(candidate.canonical_name)
        entity = await session.scalar(
            select(Entity).where(
                Entity.workspace_id == source.workspace_id,
                Entity.entity_type == candidate.entity_type,
                Entity.canonical_name_normalized == normalized,
            )
        )
        if entity is None and candidate.confidence >= 0.85:
            possible_entities = list(
                (
                    await session.scalars(
                        select(Entity).where(
                            Entity.workspace_id == source.workspace_id,
                            Entity.entity_type == candidate.entity_type,
                            Entity.status == "active",
                        )
                    )
                ).all()
            )
            candidate_names = {normalized, *(normalize_name(alias) for alias in candidate.aliases)}
            alias_matches = [
                possible
                for possible in possible_entities
                if candidate_names
                & {
                    possible.canonical_name_normalized,
                    *(normalize_name(alias) for alias in possible.aliases),
                }
            ]
            if len(alias_matches) == 1:
                entity = alias_matches[0]
        if entity is None:
            entity = Entity(
                workspace_id=source.workspace_id,
                entity_type=candidate.entity_type,
                canonical_name=candidate.canonical_name.strip(),
                canonical_name_normalized=normalized,
                aliases=sorted(set(candidate.aliases)),
            )
            session.add(entity)
            await session.flush()
            session.add(
                AuditEvent(
                    workspace_id=source.workspace_id,
                    operation="entity_created",
                    target_type="entity",
                    target_id=entity.id,
                    event_metadata={"source_id": str(source.id)},
                )
            )
        else:
            entity.aliases = sorted(set(entity.aliases) | set(candidate.aliases))
        entity_ids[candidate.candidate_id] = entity.id
        await _evidence(
            session,
            source,
            "entity",
            entity.id,
            candidate.evidence.excerpt,
            candidate.evidence.start_offset,
            candidate.evidence.end_offset,
        )

    for candidate in result.facts:
        subject_id = entity_ids.get(candidate.subject_candidate_id or "")
        valid_from = validated_datetime(candidate.valid_from, source.captured_at)
        fact_fingerprint = fingerprint(subject_id, candidate.predicate, candidate.value, valid_from)
        fact = await session.scalar(
            select(Fact).where(
                Fact.workspace_id == source.workspace_id,
                Fact.fingerprint == fact_fingerprint,
            )
        )
        if fact is None:
            fact = Fact(
                workspace_id=source.workspace_id,
                subject_entity_id=subject_id,
                predicate=candidate.predicate,
                value={"value": candidate.value},
                value_text=candidate.value_text,
                fact_type=candidate.fact_type,
                fingerprint=fact_fingerprint,
                status=(
                    FactStatus.CURRENT.value
                    if candidate.confidence >= 0.70
                    else FactStatus.PROPOSED.value
                ),
                confidence=candidate.confidence,
                valid_from=valid_from,
            )
            if subject_id is not None and candidate.confidence >= 0.70:
                previous_predicate = candidate.supersedes_predicate or candidate.predicate
                previous = await session.scalar(
                    select(Fact)
                    .where(
                        Fact.workspace_id == source.workspace_id,
                        Fact.subject_entity_id == subject_id,
                        Fact.predicate == previous_predicate,
                        Fact.status == FactStatus.CURRENT.value,
                    )
                    .order_by(Fact.created_at.desc())
                )
                if previous:
                    previous.status = FactStatus.SUPERSEDED.value
                    fact.supersedes_id = previous.id
            session.add(fact)
            await session.flush()
            session.add(
                AuditEvent(
                    workspace_id=source.workspace_id,
                    operation="fact_created",
                    target_type="fact",
                    target_id=fact.id,
                    event_metadata={"source_id": str(source.id), "status": fact.status},
                )
            )
        await _evidence(
            session,
            source,
            "fact",
            fact.id,
            candidate.evidence.excerpt,
            candidate.evidence.start_offset,
            candidate.evidence.end_offset,
        )
        if candidate.confidence >= 0.70 and is_completion_fact(
            candidate.fact_type,
            candidate.predicate,
            candidate.value,
            candidate.value_text,
            candidate.evidence.excerpt,
        ):
            completed_commitment = await _matching_completed_commitment(
                session,
                source,
                candidate.value_text,
                candidate.evidence.excerpt,
                None,
            )
            if (
                completed_commitment is not None
                and completed_commitment.status == CommitmentStatus.OPEN.value
            ):
                completed_commitment.status = CommitmentStatus.COMPLETED.value
                session.add(
                    AuditEvent(
                        workspace_id=source.workspace_id,
                        operation="commitment_completed",
                        target_type="commitment",
                        target_id=completed_commitment.id,
                        event_metadata={
                            "source_id": str(source.id),
                            "reason": "completion_fact",
                        },
                    )
                )

    for candidate in result.commitments:
        responsible_id = entity_ids.get(candidate.responsible_candidate_id or "")
        due_at = validated_datetime(candidate.due_at, source.captured_at)
        commitment_fingerprint = fingerprint(responsible_id, candidate.description, due_at)
        commitment = await session.scalar(
            select(Commitment).where(
                Commitment.workspace_id == source.workspace_id,
                Commitment.fingerprint == commitment_fingerprint,
            )
        )
        if commitment is None and candidate.status == CommitmentStatus.COMPLETED.value:
            commitment = await _matching_completed_commitment(
                session,
                source,
                candidate.description,
                candidate.evidence.excerpt,
                responsible_id,
            )
            if commitment is not None and commitment.status == CommitmentStatus.OPEN.value:
                commitment.status = CommitmentStatus.COMPLETED.value
                session.add(
                    AuditEvent(
                        workspace_id=source.workspace_id,
                        operation="commitment_completed",
                        target_type="commitment",
                        target_id=commitment.id,
                        event_metadata={"source_id": str(source.id)},
                    )
                )
        if commitment is None:
            commitment = Commitment(
                workspace_id=source.workspace_id,
                responsible_entity_id=responsible_id,
                description=candidate.description,
                fingerprint=commitment_fingerprint,
                due_at=due_at,
                status=candidate.status,
                confidence=candidate.confidence,
            )
            session.add(commitment)
            await session.flush()
            session.add(
                AuditEvent(
                    workspace_id=source.workspace_id,
                    operation="commitment_created",
                    target_type="commitment",
                    target_id=commitment.id,
                    event_metadata={"source_id": str(source.id)},
                )
            )
        await _evidence(
            session,
            source,
            "commitment",
            commitment.id,
            candidate.evidence.excerpt,
            candidate.evidence.start_offset,
            candidate.evidence.end_offset,
        )

    await session.flush()
