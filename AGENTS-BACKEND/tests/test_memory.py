from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.memory.mutations import correct_memory, delete_source
from agents_backend.memory.service import (
    consolidate_extraction,
    is_completion_fact,
    normalize_name,
)
from agents_backend.models import AuditEvent, Commitment, Entity, Evidence, Fact, Source
from agents_backend.schemas import CorrectionRequest, ExtractionResult


def extraction_result(confidence: float = 0.9) -> ExtractionResult:
    return ExtractionResult.model_validate(
        {
            "entities": [
                {
                    "candidate_id": "e1",
                    "entity_type": "person",
                    "canonical_name": "João Ávila",
                    "aliases": ["João"],
                    "confidence": 0.95,
                    "evidence": {"excerpt": "João Ávila confirmou"},
                }
            ],
            "facts": [
                {
                    "candidate_id": "f1",
                    "subject_candidate_id": "e1",
                    "fact_type": "decision",
                    "predicate": "launch_date",
                    "value": "2026-09-20",
                    "value_text": "Lançamento em 20 de setembro",
                    "confidence": confidence,
                    "evidence": {"excerpt": "lançamento em 20 de setembro"},
                }
            ],
            "commitments": [],
        }
    )


@pytest.mark.asyncio
async def test_consolidation_is_deduplicated_and_evidenced(
    session: AsyncSession, context: RequestContext
) -> None:
    source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-15T10:00:00-03:00"),
        transcript="João Ávila confirmou o lançamento em 20 de setembro.",
        transcript_hash="a" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add(source)
    await session.commit()

    result = extraction_result()
    await consolidate_extraction(session, source, result)
    await session.commit()
    await consolidate_extraction(session, source, result)
    await session.commit()

    assert await session.scalar(select(func.count(Entity.id))) == 1
    assert await session.scalar(select(func.count(Fact.id))) == 1
    assert await session.scalar(select(func.count(Evidence.id))) == 2
    fact = await session.scalar(select(Fact))
    assert fact is not None and fact.status == "current"


@pytest.mark.asyncio
async def test_low_confidence_fact_stays_proposed(
    session: AsyncSession, context: RequestContext
) -> None:
    source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-15T10:00:00-03:00"),
        transcript="Talvez o lançamento seja em setembro.",
        transcript_hash="b" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add(source)
    await session.commit()
    await consolidate_extraction(session, source, extraction_result(0.69))
    await session.commit()
    fact = await session.scalar(select(Fact))
    assert fact is not None and fact.status == "proposed"


def test_name_normalization_handles_accents_and_spaces() -> None:
    assert normalize_name("  João   ÁVILA ") == "joao avila"


def test_completion_fact_detection_rejects_pending_or_negative_state() -> None:
    assert is_completion_fact("status", "guia pronto e publicado") is True
    assert is_completion_fact("status", "a conversa ainda não foi agendada") is False
    assert is_completion_fact("status", "a entrega está pendente") is False


@pytest.mark.asyncio
async def test_absurd_model_date_is_not_persisted(
    session: AsyncSession, context: RequestContext
) -> None:
    source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-15T10:00:00-03:00"),
        transcript="Paulo revisará a página até 18 de agosto.",
        transcript_hash="d" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add(source)
    await session.commit()
    result = ExtractionResult.model_validate(
        {
            "entities": [
                {
                    "candidate_id": "e1",
                    "entity_type": "person",
                    "canonical_name": "Paulo",
                    "confidence": 0.95,
                    "evidence": {"excerpt": "Paulo revisará a página"},
                }
            ],
            "facts": [],
            "commitments": [
                {
                    "candidate_id": "c1",
                    "responsible_candidate_id": "e1",
                    "description": "Revisar a página",
                    "due_at": "8888-08-18T00:00:00Z",
                    "confidence": 0.95,
                    "evidence": {"excerpt": "até 18 de agosto"},
                }
            ],
        }
    )

    await consolidate_extraction(session, source, result)
    await session.commit()

    commitment = await session.scalar(select(Commitment))
    assert commitment is not None
    assert commitment.due_at is None


@pytest.mark.asyncio
async def test_completion_updates_existing_commitment_without_duplication(
    session: AsyncSession, context: RequestContext
) -> None:
    first_source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-21T10:00:00-03:00"),
        transcript="Eduardo enviará uma síntese de riscos para Lívia.",
        transcript_hash="1" * 64,
        language="pt-BR",
        source_metadata={},
    )
    completion_source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-22T10:00:00-03:00"),
        transcript="Lívia recebeu a síntese de riscos enviada por Eduardo.",
        transcript_hash="2" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add_all([first_source, completion_source])
    await session.commit()

    def result(name: str, description: str, status: str) -> ExtractionResult:
        return ExtractionResult.model_validate(
            {
                "entities": [
                    {
                        "candidate_id": "e1",
                        "entity_type": "person",
                        "canonical_name": name,
                        "confidence": 0.95,
                        "evidence": {"excerpt": name},
                    }
                ],
                "facts": [],
                "commitments": [
                    {
                        "candidate_id": "c1",
                        "responsible_candidate_id": "e1",
                        "description": description,
                        "status": status,
                        "confidence": 0.95,
                        "evidence": {"excerpt": completion_source.transcript},
                    }
                ],
            }
        )

    await consolidate_extraction(
        session,
        first_source,
        result("Eduardo", "Enviar uma síntese de riscos para Lívia", "open"),
    )
    await session.commit()
    await consolidate_extraction(
        session,
        completion_source,
        result("Lívia", "Síntese de riscos enviada por Eduardo", "completed"),
    )
    await session.commit()

    commitments = list((await session.scalars(select(Commitment))).all())
    assert len(commitments) == 1
    assert commitments[0].status == "completed"


@pytest.mark.asyncio
async def test_completion_fact_closes_matching_open_commitment(
    session: AsyncSession, context: RequestContext
) -> None:
    first_source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-21T10:00:00-03:00"),
        transcript="Eduardo enviará uma síntese de riscos para Lívia.",
        transcript_hash="3" * 64,
        language="pt-BR",
        source_metadata={},
    )
    completion_source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-22T10:00:00-03:00"),
        transcript="Lívia recebeu a síntese de riscos enviada por Eduardo.",
        transcript_hash="4" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add_all([first_source, completion_source])
    await session.commit()
    open_result = ExtractionResult.model_validate(
        {
            "entities": [
                {
                    "candidate_id": "eduardo",
                    "entity_type": "person",
                    "canonical_name": "Eduardo",
                    "confidence": 0.95,
                    "evidence": {"excerpt": "Eduardo"},
                }
            ],
            "facts": [],
            "commitments": [
                {
                    "candidate_id": "send-summary",
                    "responsible_candidate_id": "eduardo",
                    "description": "Enviar uma síntese de riscos para Lívia",
                    "confidence": 0.95,
                    "evidence": {"excerpt": first_source.transcript},
                }
            ],
        }
    )
    completion_result = ExtractionResult.model_validate(
        {
            "entities": [
                {
                    "candidate_id": "livia",
                    "entity_type": "person",
                    "canonical_name": "Lívia",
                    "confidence": 0.95,
                    "evidence": {"excerpt": "Lívia"},
                }
            ],
            "facts": [
                {
                    "candidate_id": "received-summary",
                    "subject_candidate_id": "livia",
                    "fact_type": "communication",
                    "predicate": "received_risk_summary",
                    "value": "síntese de riscos enviada por Eduardo",
                    "value_text": "Lívia recebeu a síntese de riscos enviada por Eduardo",
                    "confidence": 0.95,
                    "evidence": {"excerpt": completion_source.transcript},
                }
            ],
            "commitments": [],
        }
    )

    await consolidate_extraction(session, first_source, open_result)
    await session.commit()
    await consolidate_extraction(session, completion_source, completion_result)
    await session.commit()

    commitments = list((await session.scalars(select(Commitment))).all())
    assert len(commitments) == 1
    assert commitments[0].status == "completed"
    completion_audit = await session.scalar(
        select(AuditEvent).where(AuditEvent.operation == "commitment_completed")
    )
    assert completion_audit is not None
    assert completion_audit.event_metadata["reason"] == "completion_fact"


@pytest.mark.asyncio
async def test_alias_resolution_and_temporal_supersession(
    session: AsyncSession, context: RequestContext
) -> None:
    first_source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-15T10:00:00-03:00"),
        transcript="Camila, chamada de Cami, definiu o prazo para setembro.",
        transcript_hash="e" * 64,
        language="pt-BR",
        source_metadata={},
    )
    second_source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-16T10:00:00-03:00"),
        transcript="Cami atualizou o prazo para outubro.",
        transcript_hash="f" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add_all([first_source, second_source])
    await session.commit()

    def result(name: str, aliases: list[str], value: str) -> ExtractionResult:
        return ExtractionResult.model_validate(
            {
                "entities": [
                    {
                        "candidate_id": "e1",
                        "entity_type": "person",
                        "canonical_name": name,
                        "aliases": aliases,
                        "confidence": 0.95,
                        "evidence": {"excerpt": name},
                    }
                ],
                "facts": [
                    {
                        "candidate_id": "f1",
                        "subject_candidate_id": "e1",
                        "fact_type": "deadline",
                        "predicate": "delivery_deadline",
                        "value": value,
                        "value_text": value,
                        "confidence": 0.95,
                        "evidence": {"excerpt": value},
                    }
                ],
                "commitments": [],
            }
        )

    await consolidate_extraction(session, first_source, result("Camila", ["Cami"], "setembro"))
    await session.commit()
    await consolidate_extraction(session, second_source, result("Cami", [], "outubro"))
    await session.commit()

    assert await session.scalar(select(func.count(Entity.id))) == 1
    facts = list((await session.scalars(select(Fact).order_by(Fact.created_at))).all())
    assert [fact.status for fact in facts] == ["superseded", "current"]

    await delete_source(session, context, second_source.id, "Remover atualização derivada")
    for fact in facts:
        await session.refresh(fact)
    assert [fact.status for fact in facts] == ["current", "deleted"]

    await delete_source(session, context, first_source.id, "Remover fonte restante")
    entity = await session.scalar(select(Entity))
    assert entity is not None
    await session.refresh(entity)
    assert entity.status == "deleted"


@pytest.mark.asyncio
async def test_correction_and_source_deletion_are_auditable(
    session: AsyncSession, context: RequestContext
) -> None:
    source = Source(
        workspace_id=context.workspace_id,
        capture_id=uuid.uuid4(),
        source_type="synthetic",
        captured_at=datetime.fromisoformat("2026-08-15T10:00:00-03:00"),
        transcript="João Ávila confirmou o lançamento em 20 de setembro.",
        transcript_hash="c" * 64,
        language="pt-BR",
        source_metadata={},
    )
    session.add(source)
    await session.commit()
    await consolidate_extraction(session, source, extraction_result())
    await session.commit()
    fact = await session.scalar(select(Fact))
    assert fact is not None

    result = await correct_memory(
        session,
        context,
        CorrectionRequest(
            target_id=fact.id,
            target_type="fact",
            operation="dispute",
            reason="Teste de correção",
        ),
    )
    assert result.status == "disputed"
    assert await session.scalar(select(func.count(AuditEvent.id))) >= 1

    await delete_source(session, context, source.id, "Teste de exclusão")
    await session.refresh(source)
    await session.refresh(fact)
    assert source.transcript is None and source.status == "deleted"
    assert fact.status == "deleted"
    assert await session.scalar(select(func.count(Evidence.id))) == 0
