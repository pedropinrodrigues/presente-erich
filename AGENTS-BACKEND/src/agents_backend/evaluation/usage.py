from __future__ import annotations

MODEL_PRICING_USD_PER_MILLION = {
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
}


def summarize_usage(records: list[dict[str, object]], model: str) -> dict[str, object]:
    input_tokens = sum(
        value for record in records if isinstance((value := record.get("input_tokens")), int)
    )
    output_tokens = sum(
        value for record in records if isinstance((value := record.get("output_tokens")), int)
    )
    duration_ms = sum(
        value for record in records if isinstance((value := record.get("duration_ms")), int)
    )
    pricing = MODEL_PRICING_USD_PER_MILLION.get(model)
    estimated_cost_usd = (
        round(
            (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000,
            6,
        )
        if pricing is not None
        else None
    )
    return {
        "model": model,
        "requests": len(records),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "estimated_cost_usd": estimated_cost_usd,
    }


def usage_report(
    extraction_records: list[dict[str, object]],
    answer_records: list[dict[str, object]],
    *,
    extraction_model: str,
    answering_model: str,
) -> dict[str, object]:
    extraction = summarize_usage(extraction_records, extraction_model)
    answering = summarize_usage(answer_records, answering_model)
    known_costs = [
        cost
        for cost in (extraction["estimated_cost_usd"], answering["estimated_cost_usd"])
        if isinstance(cost, float)
    ]
    return {
        "extraction": extraction,
        "answering": answering,
        "estimated_model_cost_usd": round(sum(known_costs), 6) if known_costs else None,
        "pricing_snapshot": {
            "date": "2026-08-15",
            "unit": "USD per 1M tokens",
            "rates": MODEL_PRICING_USD_PER_MILLION,
        },
        "embedding_cost_included": False,
    }
