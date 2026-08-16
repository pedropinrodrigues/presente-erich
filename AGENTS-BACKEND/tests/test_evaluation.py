from __future__ import annotations

from pathlib import Path

from agents_backend.evaluation.runner import evaluate_dataset, load_dataset


def test_synthetic_dataset_is_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    cases = load_dataset(root / "evaluation" / "synthetic-transcripts.jsonl")
    report = evaluate_dataset(cases)
    assert report["cases"] == 30
    assert report["dataset_valid"] is True
