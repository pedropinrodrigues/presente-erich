from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select

from agents_backend.auth import Identity, RequestContext
from agents_backend.config import get_settings
from agents_backend.db import get_session_factory
from agents_backend.evaluation.metrics import extraction_metrics, similarity
from agents_backend.evaluation.runner import EvaluationCase, load_dataset, select_cases
from agents_backend.evaluation.usage import usage_report
from agents_backend.memory.service import consolidate_extraction
from agents_backend.model_gateway.client import (
    ANSWER_PROMPT_VERSION,
    EXTRACTION_PROMPT_VERSION,
    SCHEMA_VERSION,
    ModelGateway,
)
from agents_backend.models import Commitment, Fact, ModelRun, Source, Workspace
from agents_backend.retrieval.service import ask_memory
from agents_backend.schemas import AskMemoryRequest, ExtractionResult

ROOT = Path(__file__).resolve().parents[1]
EXTRACTIONS_PATH = ROOT / "evaluation" / "live-extractions.json"
LAST_RUN_EXTRACTIONS_PATH = ROOT / "evaluation" / "live-run-extractions.json"


async def extract_cases(
    cases: list[EvaluationCase], gateway: ModelGateway
) -> tuple[list[ExtractionResult | None], list[dict[str, str]], list[dict[str, object]]]:
    semaphore = asyncio.Semaphore(3)
    errors: list[dict[str, str]] = []
    completed = 0
    completed_lock = asyncio.Lock()
    fatal_error = asyncio.Event()

    async def extract(
        case: EvaluationCase,
    ) -> tuple[ExtractionResult | None, dict[str, object] | None]:
        nonlocal completed
        async with semaphore:
            try:
                if fatal_error.is_set():
                    return None, None
                result = await gateway.extract(case.text, case.occurred_at)
                value = result.value if isinstance(result.value, ExtractionResult) else None
                return value, {
                    "case_id": case.id,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "duration_ms": result.duration_ms,
                }
            except Exception as exc:
                message = str(exc)
                if "insufficient_quota" in message or "credit_balance_exhausted" in message:
                    fatal_error.set()
                errors.append(
                    {
                        "case_id": case.id,
                        "error_type": type(exc).__name__,
                        "message": message[:500],
                    }
                )
                return None, None
            finally:
                async with completed_lock:
                    completed += 1
                    print(f"extraction_progress={completed}/{len(cases)}", flush=True)

    extracted = list(await asyncio.gather(*(extract(case) for case in cases)))
    results = [result for result, _ in extracted]
    usage = [record for _, record in extracted if record is not None]
    return results, sorted(errors, key=lambda item: item["case_id"]), usage


def load_cached_extractions(
    cases: list[EvaluationCase], gateway: ModelGateway
) -> tuple[list[ExtractionResult | None], list[dict[str, object]]]:
    cached = json.loads(EXTRACTIONS_PATH.read_text(encoding="utf-8"))
    if cached.get("model") != gateway.settings.openai_model_extraction:
        raise ValueError("O cache foi gerado com outro modelo de extração")
    if cached.get("prompt_version") != EXTRACTION_PROMPT_VERSION:
        raise ValueError("O cache foi gerado com outra versão de prompt")
    if cached.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("O cache foi gerado com outra versão de schema")
    cached_by_id = {item["case_id"]: item["result"] for item in cached["cases"]}
    if set(cached_by_id) != {case.id for case in cases}:
        raise ValueError("O cache de extrações não corresponde ao dataset atual")
    if not any(result is not None for result in cached_by_id.values()):
        raise ValueError("O cache não contém nenhuma extração válida")
    results = [
        ExtractionResult.model_validate(cached_by_id[case.id])
        if cached_by_id[case.id] is not None
        else None
        for case in cases
    ]
    usage = [item["usage"] for item in cached["cases"] if isinstance(item.get("usage"), dict)]
    return results, usage


async def evaluate_live(
    *,
    reuse_extractions: bool = False,
    extraction_model: str | None = None,
    answering_model: str | None = None,
    case_limit: int | None = None,
    case_ids: list[str] | None = None,
) -> dict[str, object]:
    all_cases = load_dataset(ROOT / "evaluation" / "synthetic-transcripts.jsonl")
    cases = select_cases(all_cases, case_limit=case_limit, case_ids=case_ids)
    full_dataset = len(cases) == len(all_cases)
    settings = get_settings().model_copy(
        update={
            **(
                {"openai_model_extraction": extraction_model}
                if extraction_model is not None
                else {}
            ),
            **({"openai_model_answering": answering_model} if answering_model is not None else {}),
        }
    )
    gateway = ModelGateway(settings=settings)
    if reuse_extractions:
        if not full_dataset:
            raise ValueError("--reuse-extractions exige o dataset completo")
        results, extraction_usage = load_cached_extractions(cases, gateway)
        extraction_errors = [
            {
                "case_id": case.id,
                "error_type": "CachedExtractionFailure",
                "message": "A execução original não produziu resultado estruturado.",
            }
            for case, result in zip(cases, results, strict=True)
            if result is None
        ]
        print(f"using_cached_extractions={len(cases)}", flush=True)
    else:
        results, extraction_errors, extraction_usage = await extract_cases(cases, gateway)
    schema_validity, precision, coverage, extraction_details = extraction_metrics(cases, results)
    extraction_output = {
        "model": gateway.settings.openai_model_extraction,
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "cases": [
            {
                "case_id": case.id,
                "result": result.model_dump(mode="json") if result is not None else None,
                "usage": next(
                    (record for record in extraction_usage if record.get("case_id") == case.id),
                    None,
                ),
            }
            for case, result in zip(cases, results, strict=True)
        ],
    }
    await asyncio.to_thread(
        LAST_RUN_EXTRACTIONS_PATH.write_text,
        json.dumps(extraction_output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if full_dataset and any(result is not None for result in results):
        await asyncio.to_thread(
            EXTRACTIONS_PATH.write_text,
            json.dumps(extraction_output, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if not any(result is not None for result in results):
        blocked_reason = (
            "openai_credit_balance_exhausted"
            if any(
                "credit_balance_exhausted" in error["message"]
                or "insufficient_quota" in error["message"]
                for error in extraction_errors
            )
            else "all_extractions_failed"
        )
        return {
            "evaluated_at": datetime.now(UTC).isoformat(),
            "cases": len(cases),
            "dataset_cases_total": len(all_cases),
            "benchmark_only": not full_dataset,
            "models": {
                "extraction": gateway.settings.openai_model_extraction,
                "answering": gateway.settings.openai_model_answering,
                "embedding": gateway.settings.openai_model_embedding,
            },
            "versions": {
                "extraction_prompt": EXTRACTION_PROMPT_VERSION,
                "answer_prompt": ANSWER_PROMPT_VERSION,
                "extraction_schema": SCHEMA_VERSION,
            },
            "reused_extractions": reuse_extractions,
            "usage": usage_report(
                extraction_usage,
                [],
                extraction_model=gateway.settings.openai_model_extraction,
                answering_model=gateway.settings.openai_model_answering,
            ),
            "metrics": {
                "schema_validity": 0.0,
                "extraction_precision": 0.0,
                "extraction_coverage": 0.0,
                "grounded_answers": 0.0,
                "useful_retrieval": 0.0,
                "duplication": 0.0,
            },
            "extraction_details": extraction_details,
            "extraction_errors": extraction_errors,
            "gates": {
                "schema_validity": False,
                "extraction_precision": False,
                "extraction_coverage": False,
                "grounded_answers": False,
                "useful_retrieval": False,
                "duplication": True,
            },
            "passed": False,
            "blocked_reason": blocked_reason,
        }
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    context = RequestContext(identity=Identity(user_id=user_id), workspace_id=workspace_id)
    session_factory = get_session_factory()
    answers: list[tuple[EvaluationCase, object]] = []
    try:
        async with session_factory() as session:
            session.add(Workspace(id=workspace_id, owner_user_id=user_id))
            await session.commit()
            for case_number, (case, result) in enumerate(zip(cases, results, strict=True), start=1):
                if result is None:
                    continue
                source = Source(
                    workspace_id=workspace_id,
                    capture_id=uuid.UUID(
                        case.capture_id.replace("cap-syn-", "00000000-0000-4000-8000-000000000")
                    ),
                    source_type="synthetic-evaluation",
                    captured_at=datetime.fromisoformat(case.occurred_at),
                    transcript=case.text,
                    transcript_hash=uuid.uuid5(uuid.NAMESPACE_URL, case.text).hex * 2,
                    language="pt-BR",
                    source_metadata={"evaluation_case": case.id},
                    status="processed",
                )
                session.add(source)
                await session.flush()
                await consolidate_extraction(session, source, result)
                await session.commit()
                print(f"consolidation_progress={case_number}/{len(cases)}", flush=True)
            fact_count = await session.scalar(
                select(func.count(Fact.id)).where(Fact.workspace_id == workspace_id)
            )
            commitment_count = await session.scalar(
                select(func.count(Commitment.id)).where(Commitment.workspace_id == workspace_id)
            )
            total_memories = (fact_count or 0) + (commitment_count or 0)
            fact_fingerprints = await session.scalar(
                select(func.count(func.distinct(Fact.fingerprint))).where(
                    Fact.workspace_id == workspace_id
                )
            )
            commitment_fingerprints = await session.scalar(
                select(func.count(func.distinct(Commitment.fingerprint))).where(
                    Commitment.workspace_id == workspace_id
                )
            )
            unique_memories = (fact_fingerprints or 0) + (commitment_fingerprints or 0)
            duplication = (
                max(0.0, ((total_memories or unique_memories) - unique_memories) / total_memories)
                if total_memories
                else 0.0
            )

        answer_semaphore = asyncio.Semaphore(3)
        answered = 0
        answered_lock = asyncio.Lock()

        async def answer_case(case: EvaluationCase) -> tuple[EvaluationCase, object]:
            nonlocal answered
            async with answer_semaphore, session_factory() as session:
                expected_question = case.expected.questions[0]
                answer = await ask_memory(
                    session,
                    context,
                    AskMemoryRequest(question=expected_question.question),
                    gateway=gateway,
                )
            async with answered_lock:
                answered += 1
                print(f"answer_progress={answered}/{len(cases)}", flush=True)
            return case, answer

        answers = list(await asyncio.gather(*(answer_case(case) for case in cases)))
        async with session_factory() as usage_session:
            answer_model_runs = list(
                (
                    await usage_session.scalars(
                        select(ModelRun).where(
                            ModelRun.workspace_id == workspace_id,
                            ModelRun.purpose == "answer",
                        )
                    )
                ).all()
            )
        answer_usage = [
            {
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "duration_ms": run.duration_ms,
            }
            for run in answer_model_runs
        ]
        await asyncio.to_thread(
            (ROOT / "evaluation" / "live-answers.json").write_text,
            json.dumps(
                [
                    {
                        "case_id": case.id,
                        "question": case.expected.questions[0].question,
                        "expected_answer": case.expected.questions[0].answer,
                        "response": answer.model_dump(mode="json"),
                    }
                    for case, answer in answers
                ],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        grounded = sum(
            bool(answer.evidence)
            or (
                "incerteza" in case.expected.questions[0].answer.casefold()
                and bool(answer.uncertainties)
            )
            for case, answer in answers
        ) / len(cases)
        useful = sum(
            (
                bool(answer.uncertainties)
                if "incerteza" in case.expected.questions[0].answer.casefold()
                else bool(answer.evidence)
                and similarity(answer.answer, case.expected.questions[0].answer) >= 0.50
            )
            for case, answer in answers
        ) / len(cases)
        metrics = {
            "schema_validity": round(schema_validity, 4),
            "extraction_precision": round(precision, 4),
            "extraction_coverage": round(coverage, 4),
            "grounded_answers": round(grounded, 4),
            "useful_retrieval": round(useful, 4),
            "duplication": round(duplication, 4),
        }
        gates = {
            "schema_validity": schema_validity >= 0.98,
            "extraction_precision": precision >= 0.90,
            "extraction_coverage": coverage >= 0.80,
            "grounded_answers": grounded >= 0.95,
            "useful_retrieval": useful >= 0.85,
            "duplication": duplication <= 0.02,
        }
        return {
            "evaluated_at": datetime.now(UTC).isoformat(),
            "cases": len(cases),
            "dataset_cases_total": len(all_cases),
            "benchmark_only": not full_dataset,
            "models": {
                "extraction": gateway.settings.openai_model_extraction,
                "answering": gateway.settings.openai_model_answering,
                "embedding": gateway.settings.openai_model_embedding,
            },
            "versions": {
                "extraction_prompt": EXTRACTION_PROMPT_VERSION,
                "answer_prompt": ANSWER_PROMPT_VERSION,
                "extraction_schema": SCHEMA_VERSION,
            },
            "reused_extractions": reuse_extractions,
            "usage": usage_report(
                extraction_usage,
                answer_usage,
                extraction_model=gateway.settings.openai_model_extraction,
                answering_model=gateway.settings.openai_model_answering,
            ),
            "metrics": metrics,
            "extraction_details": extraction_details,
            "extraction_errors": extraction_errors,
            "gates": gates,
            "passed": full_dataset and all(gates.values()),
            "benchmark_passed": all(gates.values()) if not full_dataset else None,
        }
    finally:
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await cleanup_session.commit()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-extractions", action="store_true")
    parser.add_argument("--extraction-model")
    parser.add_argument("--answering-model")
    case_selection = parser.add_mutually_exclusive_group()
    case_selection.add_argument("--case-limit", type=int)
    case_selection.add_argument(
        "--case-ids",
        help="IDs separados por vírgula, por exemplo syn-001,syn-009",
    )
    args = parser.parse_args()
    report = await evaluate_live(
        reuse_extractions=args.reuse_extractions,
        extraction_model=args.extraction_model,
        answering_model=args.answering_model,
        case_limit=args.case_limit,
        case_ids=args.case_ids.split(",") if args.case_ids else None,
    )
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    report_path = ROOT / "evaluation" / "live-report.json"
    await asyncio.to_thread(report_path.write_text, output, encoding="utf-8")
    print(output, end="")
    if report.get("benchmark_only") and not report.get("blocked_reason"):
        return
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
