from __future__ import annotations

import base64
import logging
import re
import uuid
from datetime import datetime

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.errors import NotFoundError
from agents_backend.model_gateway.client import AnswerDraft, ModelGateway
from agents_backend.models import Commitment, Entity, Evidence, Fact, FactStatus, ModelRun
from agents_backend.schemas import (
    AskMemoryRequest,
    AskMemoryResponse,
    EntityResponse,
    EvidenceResponse,
    SearchItem,
    SearchMemoryResponse,
)

logger = logging.getLogger(__name__)


def _encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    value = f"{created_at.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(value.encode()).decode()


def _decode_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        timestamp, item_id = decoded.split("|", 1)
        return datetime.fromisoformat(timestamp), uuid.UUID(item_id)
    except (ValueError, UnicodeDecodeError):
        return None


async def _evidence_map(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    targets: list[tuple[str, uuid.UUID]],
) -> dict[tuple[str, uuid.UUID], list[EvidenceResponse]]:
    if not targets:
        return {}
    conditions = [
        and_(Evidence.target_type == target_type, Evidence.target_id == target_id)
        for target_type, target_id in targets
    ]
    rows = (
        await session.scalars(
            select(Evidence).where(Evidence.workspace_id == workspace_id, or_(*conditions))
        )
    ).all()
    result: dict[tuple[str, uuid.UUID], list[EvidenceResponse]] = {}
    for row in rows:
        key = (row.target_type, row.target_id)
        result.setdefault(key, []).append(
            EvidenceResponse(
                id=row.id,
                source_id=row.source_id,
                excerpt=row.excerpt,
                fact_id=row.target_id if row.target_type == "fact" else None,
                commitment_id=row.target_id if row.target_type == "commitment" else None,
            )
        )
    return result


def _apply_cursor(statement: Select[tuple[Fact]], cursor: str | None) -> Select[tuple[Fact]]:
    decoded = _decode_cursor(cursor)
    if decoded:
        created_at, item_id = decoded
        statement = statement.where(
            or_(
                Fact.created_at < created_at, and_(Fact.created_at == created_at, Fact.id < item_id)
            )
        )
    return statement


async def search_memory(
    session: AsyncSession,
    context: RequestContext,
    *,
    query: str | None,
    entity_id: uuid.UUID | None,
    item_type: str | None,
    status: str | None,
    from_: datetime | None,
    to: datetime | None,
    cursor: str | None,
    limit: int = 20,
    gateway: ModelGateway | None = None,
) -> SearchMemoryResponse:
    items: list[SearchItem] = []
    entity_rows: list[Entity] = []
    fact_rows: list[Fact] = []
    commitment_rows: list[Commitment] = []

    if item_type in (None, "entity"):
        entity_statement = select(Entity).where(
            Entity.workspace_id == context.workspace_id,
            Entity.status == "active",
        )
        if query:
            entity_statement = entity_statement.where(
                Entity.canonical_name.ilike(f"%{query.strip()}%")
            )
        if entity_id:
            entity_statement = entity_statement.where(Entity.id == entity_id)
        decoded_cursor = _decode_cursor(cursor)
        if decoded_cursor:
            created_at, cursor_id = decoded_cursor
            entity_statement = entity_statement.where(
                or_(
                    Entity.created_at < created_at,
                    and_(Entity.created_at == created_at, Entity.id < cursor_id),
                )
            )
        entity_rows = list(
            (
                await session.scalars(
                    entity_statement.order_by(Entity.created_at.desc(), Entity.id.desc()).limit(
                        limit + 1
                    )
                )
            ).all()
        )

    if item_type in (None, "fact"):
        fact_statement = select(Fact).where(
            Fact.workspace_id == context.workspace_id,
            Fact.status != FactStatus.DELETED.value,
        )
        if query:
            fact_statement = fact_statement.where(Fact.value_text.ilike(f"%{query.strip()}%"))
        if entity_id:
            fact_statement = fact_statement.where(Fact.subject_entity_id == entity_id)
        if status:
            fact_statement = fact_statement.where(Fact.status == status)
        if from_:
            fact_statement = fact_statement.where(Fact.created_at >= from_)
        if to:
            fact_statement = fact_statement.where(Fact.created_at <= to)
        fact_statement = _apply_cursor(fact_statement, cursor)
        fact_rows = list(
            (
                await session.scalars(
                    fact_statement.order_by(Fact.created_at.desc(), Fact.id.desc()).limit(limit + 1)
                )
            ).all()
        )
        if query:
            try:
                query_embedding = await (gateway or ModelGateway()).embed(query.strip())
                semantic_statement = select(Fact).where(
                    Fact.workspace_id == context.workspace_id,
                    Fact.status != FactStatus.DELETED.value,
                    Fact.embedding.is_not(None),
                )
                if entity_id:
                    semantic_statement = semantic_statement.where(
                        Fact.subject_entity_id == entity_id
                    )
                if status:
                    semantic_statement = semantic_statement.where(Fact.status == status)
                if from_:
                    semantic_statement = semantic_statement.where(Fact.created_at >= from_)
                if to:
                    semantic_statement = semantic_statement.where(Fact.created_at <= to)
                semantic_rows = list(
                    (
                        await session.scalars(
                            semantic_statement.order_by(
                                Fact.embedding.cosine_distance(query_embedding)
                            ).limit(limit)
                        )
                    ).all()
                )
                seen_fact_ids = {row.id for row in fact_rows}
                fact_rows.extend(row for row in semantic_rows if row.id not in seen_fact_ids)
            except Exception:
                logger.warning("semantic_search_unavailable")

    if item_type in (None, "commitment"):
        commitment_statement = select(Commitment).where(
            Commitment.workspace_id == context.workspace_id,
            Commitment.status != "deleted",
        )
        if query:
            commitment_statement = commitment_statement.where(
                Commitment.description.ilike(f"%{query.strip()}%")
            )
        if entity_id:
            commitment_statement = commitment_statement.where(
                Commitment.responsible_entity_id == entity_id
            )
        if status:
            commitment_statement = commitment_statement.where(Commitment.status == status)
        if from_:
            commitment_statement = commitment_statement.where(Commitment.created_at >= from_)
        if to:
            commitment_statement = commitment_statement.where(Commitment.created_at <= to)
        decoded_cursor = _decode_cursor(cursor)
        if decoded_cursor:
            created_at, cursor_id = decoded_cursor
            commitment_statement = commitment_statement.where(
                or_(
                    Commitment.created_at < created_at,
                    and_(
                        Commitment.created_at == created_at,
                        Commitment.id < cursor_id,
                    ),
                )
            )
        commitment_rows = list(
            (
                await session.scalars(
                    commitment_statement.order_by(
                        Commitment.created_at.desc(), Commitment.id.desc()
                    ).limit(limit + 1)
                )
            ).all()
        )

    combined = (
        [("entity", row) for row in entity_rows]
        + [("fact", row) for row in fact_rows]
        + [("commitment", row) for row in commitment_rows]
    )
    combined.sort(key=lambda pair: (pair[1].created_at, pair[1].id), reverse=True)
    page = combined[:limit]
    evidence = await _evidence_map(
        session, context.workspace_id, [(kind, row.id) for kind, row in page]
    )
    for kind, row in page:
        if kind == "entity":
            items.append(
                SearchItem(
                    id=row.id,
                    type="entity",
                    title=row.canonical_name,
                    content=", ".join(row.aliases),
                    status=row.status,
                    occurred_at=row.created_at,
                    evidence=evidence.get((kind, row.id), []),
                )
            )
        elif kind == "fact":
            items.append(
                SearchItem(
                    id=row.id,
                    type="fact",
                    title=row.predicate,
                    content=row.value_text,
                    status=row.status,
                    confidence=row.confidence,
                    occurred_at=row.created_at,
                    evidence=evidence.get((kind, row.id), []),
                )
            )
        else:
            items.append(
                SearchItem(
                    id=row.id,
                    type="commitment",
                    title="Compromisso",
                    content=row.description,
                    status=row.status,
                    confidence=row.confidence,
                    occurred_at=row.created_at,
                    evidence=evidence.get((kind, row.id), []),
                )
            )
    next_cursor = None
    if len(combined) > limit and page:
        last = page[-1][1]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return SearchMemoryResponse(items=items, next_cursor=next_cursor)


async def get_entity_view(
    session: AsyncSession, context: RequestContext, entity_id: uuid.UUID
) -> EntityResponse:
    entity = await session.scalar(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.workspace_id == context.workspace_id,
            Entity.status == "active",
        )
    )
    if entity is None:
        raise NotFoundError()
    current = await search_memory(
        session,
        context,
        query=None,
        entity_id=entity.id,
        item_type=None,
        status=None,
        from_=None,
        to=None,
        cursor=None,
        limit=100,
    )
    history_rows = list(
        (
            await session.scalars(
                select(Fact)
                .where(
                    Fact.workspace_id == context.workspace_id,
                    Fact.subject_entity_id == entity.id,
                    Fact.status.in_(["superseded", "disputed"]),
                )
                .order_by(Fact.created_at.desc())
                .limit(50)
            )
        ).all()
    )
    history = [
        SearchItem(
            id=row.id,
            type="fact",
            title=row.predicate,
            content=row.value_text,
            status=row.status,
            confidence=row.confidence,
            occurred_at=row.created_at,
        )
        for row in history_rows
    ]
    return EntityResponse(
        id=entity.id,
        type=entity.entity_type,
        canonical_name=entity.canonical_name,
        aliases=entity.aliases,
        facts=[item for item in current.items if item.type == "fact" and item.status == "current"],
        commitments=[item for item in current.items if item.type == "commitment"],
        history=history,
    )


async def ask_memory(
    session: AsyncSession,
    context: RequestContext,
    request: AskMemoryRequest,
    gateway: ModelGateway | None = None,
) -> AskMemoryResponse:
    tokens = [
        token for token in re.findall(r"[\wÀ-ÿ]+", request.question.casefold()) if len(token) >= 4
    ]
    fact_conditions = [Fact.value_text.ilike(f"%{token}%") for token in tokens[:8]]
    commitment_conditions = [Commitment.description.ilike(f"%{token}%") for token in tokens[:8]]
    matching_evidence = []
    if tokens:
        matching_evidence = list(
            (
                await session.execute(
                    select(Evidence.target_type, Evidence.target_id).where(
                        Evidence.workspace_id == context.workspace_id,
                        or_(*[Evidence.excerpt.ilike(f"%{token}%") for token in tokens[:8]]),
                    )
                )
            ).all()
        )
    evidence_fact_ids = [target_id for kind, target_id in matching_evidence if kind == "fact"]
    evidence_commitment_ids = [
        target_id for kind, target_id in matching_evidence if kind == "commitment"
    ]
    fact_statement = select(Fact).where(
        Fact.workspace_id == context.workspace_id,
        Fact.status == FactStatus.CURRENT.value,
    )
    commitment_statement = select(Commitment).where(
        Commitment.workspace_id == context.workspace_id,
        Commitment.status != "deleted",
    )
    if fact_conditions:
        fact_statement = fact_statement.where(or_(*fact_conditions, Fact.id.in_(evidence_fact_ids)))
        commitment_statement = commitment_statement.where(
            or_(*commitment_conditions, Commitment.id.in_(evidence_commitment_ids))
        )
    if request.context.entity_ids:
        fact_statement = fact_statement.where(
            Fact.subject_entity_id.in_(request.context.entity_ids)
        )
        commitment_statement = commitment_statement.where(
            Commitment.responsible_entity_id.in_(request.context.entity_ids)
        )
    if request.context.from_:
        fact_statement = fact_statement.where(Fact.created_at >= request.context.from_)
        commitment_statement = commitment_statement.where(
            Commitment.created_at >= request.context.from_
        )
    if request.context.to:
        fact_statement = fact_statement.where(Fact.created_at <= request.context.to)
        commitment_statement = commitment_statement.where(
            Commitment.created_at <= request.context.to
        )
    facts = list(
        (await session.scalars(fact_statement.order_by(Fact.created_at.desc()).limit(8))).all()
    )
    commitments = list(
        (
            await session.scalars(
                commitment_statement.order_by(Commitment.created_at.desc()).limit(8)
            )
        ).all()
    )
    targets = [("fact", row.id) for row in facts] + [("commitment", row.id) for row in commitments]
    evidence_map = await _evidence_map(session, context.workspace_id, targets)
    evidence_items = [item for values in evidence_map.values() for item in values]
    if not evidence_items:
        return AskMemoryResponse(
            answer="Não encontrei evidência suficiente na memória para responder.",
            evidence=[],
            uncertainties=["Não há fonte recuperável que sustente uma resposta."],
            source_ids=[],
        )
    evidence_by_id = {str(item.id): item for item in evidence_items}
    payload = [
        {"id": str(item.id), "source_id": str(item.source_id), "excerpt": item.excerpt}
        for item in evidence_items
    ]
    model_result = await (gateway or ModelGateway()).answer(request.question, payload)
    draft = model_result.value
    if not isinstance(draft, AnswerDraft):
        raise TypeError("Tipo inesperado de resposta do model gateway")
    selected = [
        evidence_by_id[item_id] for item_id in draft.evidence_ids if item_id in evidence_by_id
    ]
    if not selected:
        selected = evidence_items
        uncertainties = [*draft.uncertainties, "O modelo não selecionou evidências específicas."]
    else:
        uncertainties = draft.uncertainties
    session.add(
        ModelRun(
            workspace_id=context.workspace_id,
            purpose="answer",
            model=model_result.model,
            prompt_version=model_result.prompt_version,
            schema_version=model_result.schema_version,
            provider_request_id=model_result.provider_request_id,
            success=True,
            input_tokens=model_result.input_tokens,
            output_tokens=model_result.output_tokens,
            duration_ms=model_result.duration_ms,
        )
    )
    await session.commit()
    return AskMemoryResponse(
        answer=draft.answer,
        evidence=selected,
        uncertainties=uncertainties,
        source_ids=sorted({item.source_id for item in selected}, key=str),
    )
