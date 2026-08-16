from __future__ import annotations

import re
import unicodedata

from agents_backend.evaluation.runner import EvaluationCase
from agents_backend.schemas import ExtractionResult

GENERIC_ENTITY_WORDS = {
    "a",
    "da",
    "de",
    "do",
    "empresa",
    "iniciativa",
    "organizacao",
    "o",
    "projeto",
}


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        re.findall(r"[a-z0-9]+", "".join(ch for ch in decomposed if not unicodedata.combining(ch)))
    )


def similarity(left: str, right: str) -> float:
    left_tokens = set(normalize(left).split())
    right_tokens = set(normalize(right).split())
    if not left_tokens or not right_tokens:
        return 0.0

    def equivalent(left_token: str, right_token: str) -> bool:
        if left_token == right_token:
            return True
        prefix_length = 0
        for left_character, right_character in zip(left_token, right_token, strict=False):
            if left_character != right_character:
                break
            prefix_length += 1
        shortest_length = min(len(left_token), len(right_token))
        return prefix_length >= 5 and prefix_length / shortest_length >= 0.70

    matched_expected = sum(
        any(equivalent(actual_token, expected_token) for actual_token in left_tokens)
        for expected_token in right_tokens
    )
    return matched_expected / len(right_tokens)


def entity_similarity(left: str, right: str) -> float:
    left_tokens = set(normalize(left).split()) - GENERIC_ENTITY_WORDS
    right_tokens = set(normalize(right).split()) - GENERIC_ENTITY_WORDS
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def resolve_expected_aliases(actual: list[str], aliases: list[str]) -> list[str]:
    alias_to_canonical: dict[str, str] = {}
    for declaration in aliases:
        alias, separator, canonical = declaration.partition("->")
        if separator and alias.strip() and canonical.strip():
            alias_to_canonical[normalize(alias)] = canonical.strip()
    return [alias_to_canonical.get(normalize(name), name) for name in actual]


def one_to_one_matches(
    actual: list[str], expected: list[str], *, threshold: float, entity: bool = False
) -> int:
    scorer = entity_similarity if entity else similarity
    candidates = sorted(
        (
            (scorer(actual_value, expected_value), actual_index, expected_index)
            for actual_index, actual_value in enumerate(actual)
            for expected_index, expected_value in enumerate(expected)
        ),
        reverse=True,
    )
    used_actual: set[int] = set()
    used_expected: set[int] = set()
    for score, actual_index, expected_index in candidates:
        if score < threshold:
            break
        if actual_index not in used_actual and expected_index not in used_expected:
            used_actual.add(actual_index)
            used_expected.add(expected_index)
    return len(used_actual)


def covered_expected_values(
    actual: list[str], expected: list[str], *, threshold: float, entity: bool = False
) -> int:
    scorer = entity_similarity if entity else similarity
    return sum(
        any(scorer(actual_value, expected_value) >= threshold for actual_value in actual)
        for expected_value in expected
    )


def extraction_metrics(
    cases: list[EvaluationCase], results: list[ExtractionResult | None]
) -> tuple[float, float, float, dict[str, dict[str, int | float]]]:
    counts = {
        "entities": {"predicted": 0, "expected": 0, "matched": 0, "covered": 0},
        "facts": {"predicted": 0, "expected": 0, "matched": 0, "covered": 0},
        "commitments": {"predicted": 0, "expected": 0, "matched": 0, "covered": 0},
    }
    schema_valid = sum(result is not None for result in results) / len(cases)
    for case, result in zip(cases, results, strict=True):
        expected_by_category = {
            "entities": case.expected.entities,
            "facts": case.expected.facts,
            "commitments": case.expected.commitments,
        }
        for category, expected_values in expected_by_category.items():
            counts[category]["expected"] += len(expected_values)
        if result is None:
            continue
        entity_names = {
            candidate.candidate_id: candidate.canonical_name for candidate in result.entities
        }
        actual_by_category = {
            "entities": resolve_expected_aliases(
                [candidate.canonical_name for candidate in result.entities],
                case.expected.aliases,
            ),
            "facts": [
                " ".join(
                    part
                    for part in (
                        entity_names.get(candidate.subject_candidate_id or ""),
                        candidate.fact_type,
                        candidate.predicate,
                        candidate.value_text,
                        candidate.evidence.excerpt,
                    )
                    if part
                )
                for candidate in result.facts
                if candidate.confidence >= 0.70
            ],
            "commitments": [
                " ".join(
                    part
                    for part in (
                        entity_names.get(candidate.responsible_candidate_id or ""),
                        candidate.description,
                        candidate.status,
                        candidate.evidence.excerpt,
                    )
                    if part
                )
                for candidate in result.commitments
                if candidate.confidence >= 0.70
            ],
        }
        for category, actual_values in actual_by_category.items():
            counts[category]["predicted"] += len(actual_values)
            counts[category]["matched"] += one_to_one_matches(
                actual_values,
                expected_by_category[category],
                threshold=0.60,
                entity=category == "entities",
            )
            counts[category]["covered"] += covered_expected_values(
                actual_values,
                expected_by_category[category],
                threshold=0.60,
                entity=category == "entities",
            )

    predicted = sum(int(category["predicted"]) for category in counts.values())
    matched = sum(int(category["matched"]) for category in counts.values())
    memory_expected = int(counts["facts"]["expected"]) + int(counts["commitments"]["expected"])
    memory_covered = int(counts["facts"]["covered"]) + int(counts["commitments"]["covered"])
    precision = matched / predicted if predicted else 0.0
    coverage = memory_covered / memory_expected if memory_expected else 1.0
    details: dict[str, dict[str, int | float]] = {}
    for category, category_counts in counts.items():
        category_predicted = int(category_counts["predicted"])
        category_expected = int(category_counts["expected"])
        category_matched = int(category_counts["matched"])
        category_covered = int(category_counts["covered"])
        details[category] = {
            **category_counts,
            "precision": round(
                category_matched / category_predicted if category_predicted else 0.0, 4
            ),
            "coverage": round(
                category_covered / category_expected if category_expected else 1.0, 4
            ),
        }
    return schema_valid, precision, coverage, details
