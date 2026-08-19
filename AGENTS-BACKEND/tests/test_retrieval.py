from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.models import Entity, Evidence, Fact, Source
from agents_backend.retrieval.service import ask_memory, search_memory
from agents_backend.schemas import AskMemoryRequest


@pytest.mark.asyncio
async def test_question_without_evidence_returns_uncertainty(
    session: AsyncSession, context: RequestContext
) -> None:
    result = await ask_memory(
        session,
        context,
        AskMemoryRequest(question="Qual é a data de renovação do contrato?"),
    )
    assert result.evidence == []
    assert result.uncertainties
    assert "Não encontrei evidência" in result.answer


@pytest.mark.asyncio
async def test_search_does_not_leak_deleted_fact_through_entity_evidence(
    session: AsyncSession, context: RequestContext
) -> None:
    source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-19T10:00:00-03:00"),
        transcript="Pedro decidiu priorizar a wiki.",
        transcript_hash="e" * 64,
        language="pt-BR",
        source_metadata={},
    )
    entity = Entity(
        workspace_id=context.workspace_id,
        entity_type="project",
        canonical_name="Wiki",
        canonical_name_normalized="wiki",
        aliases=[],
        status="active",
    )
    session.add_all([source, entity])
    await session.flush()
    fact = Fact(
        workspace_id=context.workspace_id,
        subject_entity_id=entity.id,
        fact_type="decision",
        predicate="prioritized_project",
        value={"value": "Pedro decidiu priorizar a wiki."},
        value_text="Pedro decidiu priorizar a wiki.",
        fingerprint="e" * 64,
        status="deleted",
        confidence=1.0,
    )
    session.add(fact)
    await session.flush()
    session.add(
        Evidence(
            workspace_id=context.workspace_id,
            source_id=source.id,
            target_type="entity",
            target_id=entity.id,
            excerpt="Pedro decidiu priorizar a wiki.",
            excerpt_hash="f" * 64,
        )
    )
    await session.commit()

    result = await search_memory(
        session,
        context,
        query=None,
        entity_id=None,
        item_type=None,
        status=None,
        from_=None,
        to=None,
        cursor=None,
    )

    assert [item.type for item in result.items] == ["entity"]
    assert result.items[0].evidence == []
