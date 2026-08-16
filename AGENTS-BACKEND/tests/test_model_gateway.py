from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agents_backend.config import Settings
from agents_backend.model_gateway.client import AnswerDraft, ModelGateway, deduplicate_extraction
from agents_backend.schemas import ExtractionResult


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        parsed = (
            ExtractionResult(entities=[], facts=[], commitments=[])
            if kwargs["text_format"] is ExtractionResult
            else AnswerDraft(answer="Resposta", evidence_ids=[])
        )
        return SimpleNamespace(
            output_parsed=parsed,
            id="response-test",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_ANON_KEY="anon",
        SUPABASE_SERVICE_ROLE_KEY="service-role",
        DATABASE_URL="postgresql://postgres:password@db.project.supabase.co/postgres",
        OPENAI_API_KEY="openai-test",
        OPENAI_MODEL_EXTRACTION="gpt-5.6-luna",
        OPENAI_MODEL_ANSWERING="gpt-5.6-luna",
        OPENAI_REASONING_EFFORT_EXTRACTION="none",
        OPENAI_REASONING_EFFORT_ANSWERING="low",
    )  # type: ignore[call-arg]


def test_deduplicate_extraction_removes_identical_model_candidates() -> None:
    result = ExtractionResult.model_validate(
        {
            "entities": [],
            "facts": [
                {
                    "candidate_id": candidate_id,
                    "fact_type": "decision",
                    "predicate": "budget_limit",
                    "value": "45 mil reais",
                    "value_text": "Orçamento máximo de 45 mil reais",
                    "confidence": 0.95,
                    "evidence": {"excerpt": "orçamento máximo de 45 mil reais"},
                }
                for candidate_id in ("fact-1", "fact-2")
            ],
            "commitments": [],
        }
    )

    deduplicated = deduplicate_extraction(result)

    assert [fact.candidate_id for fact in deduplicated.facts] == ["fact-1"]


@pytest.mark.asyncio
async def test_gateway_sends_explicit_reasoning_effort_and_disables_storage() -> None:
    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)
    gateway = ModelGateway(settings=make_settings(), client=client)  # type: ignore[arg-type]

    await gateway.extract("Marina confirmou o lançamento.", "2026-08-15T10:00:00-03:00")
    await gateway.answer("O que foi confirmado?", [])

    assert responses.calls[0]["model"] == "gpt-5.6-luna"
    assert responses.calls[0]["reasoning"] == {"effort": "none"}
    assert responses.calls[0]["store"] is False
    assert responses.calls[1]["model"] == "gpt-5.6-luna"
    assert responses.calls[1]["reasoning"] == {"effort": "low"}
    assert responses.calls[1]["store"] is False
