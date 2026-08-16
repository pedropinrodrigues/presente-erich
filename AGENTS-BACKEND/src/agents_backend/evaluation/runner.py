from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ExpectedQuestion(BaseModel):
    question: str
    answer: str


class ExpectedMemory(BaseModel):
    entities: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    commitments: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    questions: list[ExpectedQuestion] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    id: str
    capture_id: str
    occurred_at: str
    text: str
    expected: ExpectedMemory


def select_cases(
    all_cases: list[EvaluationCase],
    *,
    case_limit: int | None = None,
    case_ids: list[str] | None = None,
) -> list[EvaluationCase]:
    if case_limit is not None and case_ids:
        raise ValueError("Use apenas uma opção entre --case-limit e --case-ids")
    if case_limit is not None:
        if not 1 <= case_limit <= len(all_cases):
            raise ValueError(f"--case-limit deve estar entre 1 e {len(all_cases)}")
        return all_cases[:case_limit]
    if not case_ids:
        return all_cases
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("--case-ids não aceita IDs duplicados")
    cases_by_id = {case.id: case for case in all_cases}
    unknown_ids = [case_id for case_id in case_ids if case_id not in cases_by_id]
    if unknown_ids:
        raise ValueError(f"IDs de caso desconhecidos: {', '.join(unknown_ids)}")
    return [cases_by_id[case_id] for case_id in case_ids]


def load_dataset(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(EvaluationCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"Caso inválido na linha {line_number}") from exc
    return cases


def evaluate_dataset(cases: list[EvaluationCase]) -> dict[str, object]:
    ids = [case.id for case in cases]
    capture_ids = [case.capture_id for case in cases]
    question_count = sum(len(case.expected.questions) for case in cases)
    return {
        "cases": len(cases),
        "unique_ids": len(set(ids)) == len(ids),
        "unique_capture_ids": len(set(capture_ids)) == len(capture_ids),
        "cases_with_questions": sum(bool(case.expected.questions) for case in cases),
        "questions": question_count,
        "dataset_valid": (
            len(cases) >= 30
            and len(set(ids)) == len(ids)
            and len(set(capture_ids)) == len(capture_ids)
            and question_count >= len(cases)
        ),
    }


def run() -> None:
    project_root = Path(__file__).resolve().parents[3]
    report = evaluate_dataset(
        load_dataset(project_root / "evaluation" / "synthetic-transcripts.jsonl")
    )
    report_path = project_root / "evaluation" / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["dataset_valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
