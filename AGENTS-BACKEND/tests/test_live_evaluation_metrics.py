from __future__ import annotations

from pathlib import Path

import pytest

from agents_backend.evaluation.metrics import extraction_metrics, similarity
from agents_backend.evaluation.runner import EvaluationCase, load_dataset, select_cases
from agents_backend.evaluation.usage import summarize_usage
from agents_backend.schemas import (
    CommitmentCandidate,
    EntityCandidate,
    ExtractionEvidence,
    ExtractionResult,
    FactCandidate,
)

ROOT = Path(__file__).resolve().parents[1]


def test_similarity_recognizes_portuguese_inflections_without_lowering_threshold() -> None:
    actual = "Rafaela decidiu o arquivamento do Projeto Semente"
    expected = "Projeto Semente está arquivado"

    assert similarity(actual, expected) == 0.75


def test_summarize_usage_applies_luna_pricing_snapshot() -> None:
    summary = summarize_usage(
        [
            {"input_tokens": 1_000_000, "output_tokens": 500_000, "duration_ms": 100},
            {"input_tokens": 500_000, "output_tokens": 500_000, "duration_ms": 200},
        ],
        "gpt-5.6-luna",
    )

    assert summary == {
        "model": "gpt-5.6-luna",
        "requests": 2,
        "input_tokens": 1_500_000,
        "output_tokens": 1_000_000,
        "duration_ms": 300,
        "estimated_cost_usd": 1.5,
    }


def test_extraction_metrics_ignore_aliases_and_cover_memories_only() -> None:
    case = EvaluationCase.model_validate(
        {
            "id": "case-1",
            "capture_id": "capture-1",
            "occurred_at": "2026-08-15T10:00:00-03:00",
            "text": "Projeto Atlas será lançado. Paulo revisará a página.",
            "expected": {
                "entities": ["Atlas", "Paulo"],
                "facts": ["Lançamento do Atlas confirmado"],
                "commitments": ["Paulo revisa a página"],
                "questions": [{"question": "Quando?", "answer": "Confirmado"}],
            },
        }
    )
    evidence = ExtractionEvidence(excerpt=case.text)
    result = ExtractionResult(
        entities=[
            EntityCandidate(
                candidate_id="entity-atlas",
                entity_type="project",
                canonical_name="Projeto Atlas",
                aliases=["Atlas interno"],
                confidence=0.99,
                evidence=evidence,
            ),
            EntityCandidate(
                candidate_id="entity-paulo",
                entity_type="person",
                canonical_name="Paulo",
                aliases=[],
                confidence=0.99,
                evidence=evidence,
            ),
        ],
        facts=[
            FactCandidate(
                candidate_id="fact-1",
                subject_candidate_id="entity-atlas",
                fact_type="decision",
                predicate="lancamento_confirmado",
                value="confirmado",
                value_text="Lançamento do Atlas confirmado",
                confidence=0.99,
                evidence=evidence,
            )
        ],
        commitments=[
            CommitmentCandidate(
                candidate_id="commitment-1",
                responsible_candidate_id="entity-paulo",
                description="Paulo revisa a página",
                confidence=0.99,
                evidence=evidence,
            )
        ],
    )

    schema, precision, coverage, details = extraction_metrics([case], [result])

    assert schema == 1.0
    assert precision == 1.0
    assert coverage == 1.0
    assert details["entities"]["predicted"] == 2


def test_extraction_metrics_resolve_expected_alias_without_extra_prediction() -> None:
    case = EvaluationCase.model_validate(
        {
            "id": "case-alias",
            "capture_id": "capture-alias",
            "occurred_at": "2026-08-15T10:00:00-03:00",
            "text": "Cami comentou sobre o projeto.",
            "expected": {
                "entities": ["Camila"],
                "aliases": ["Cami -> Camila"],
                "questions": [{"question": "Quem comentou?", "answer": "Camila"}],
            },
        }
    )
    result = ExtractionResult(
        entities=[
            EntityCandidate(
                candidate_id="entity-cami",
                entity_type="person",
                canonical_name="Cami",
                confidence=0.99,
                evidence=ExtractionEvidence(excerpt=case.text),
            )
        ],
        facts=[],
        commitments=[],
    )

    schema, precision, coverage, details = extraction_metrics([case], [result])

    assert schema == 1.0
    assert precision == 1.0
    assert coverage == 1.0
    assert details["entities"] == {
        "predicted": 1,
        "expected": 1,
        "matched": 1,
        "covered": 1,
        "precision": 1.0,
        "coverage": 1.0,
    }


def test_extraction_coverage_allows_one_complete_fact_to_cover_two_expectations() -> None:
    case = EvaluationCase.model_validate(
        {
            "id": "case-composite",
            "capture_id": "capture-composite",
            "occurred_at": "2026-08-15T10:00:00-03:00",
            "text": "O Portal foi adiado para 9 de setembro devido a erro de autenticação.",
            "expected": {
                "facts": [
                    "Portal adiado para 9 de setembro",
                    "Portal adiado devido a erro de autenticação",
                ],
                "questions": [{"question": "O que mudou?", "answer": "A data"}],
            },
        }
    )
    result = ExtractionResult(
        entities=[],
        facts=[
            FactCandidate(
                candidate_id="fact-composite",
                fact_type="decision",
                predicate="publication_delay",
                value="9 de setembro devido a erro de autenticação",
                value_text="Portal adiado para 9 de setembro devido a erro de autenticação",
                confidence=0.99,
                evidence=ExtractionEvidence(excerpt=case.text),
            )
        ],
        commitments=[],
    )

    _, precision, coverage, details = extraction_metrics([case], [result])

    assert precision == 1.0
    assert coverage == 1.0
    assert details["facts"]["matched"] == 1
    assert details["facts"]["covered"] == 2


def test_select_cases_preserves_explicit_representative_order() -> None:
    cases = load_dataset(ROOT / "evaluation" / "synthetic-transcripts.jsonl")

    selected = select_cases(cases, case_ids=["syn-024", "syn-001", "syn-009"])

    assert [case.id for case in selected] == ["syn-024", "syn-001", "syn-009"]


@pytest.mark.parametrize(
    ("case_limit", "case_ids", "message"),
    [
        (2, ["syn-001"], "apenas uma opção"),
        (None, ["syn-999"], "desconhecidos"),
        (None, ["syn-001", "syn-001"], "duplicados"),
    ],
)
def test_select_cases_rejects_invalid_selection(
    case_limit: int | None, case_ids: list[str], message: str
) -> None:
    cases = load_dataset(ROOT / "evaluation" / "synthetic-transcripts.jsonl")

    with pytest.raises(ValueError, match=message):
        select_cases(cases, case_limit=case_limit, case_ids=case_ids)
