from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_backend.config import get_settings
from agents_backend.conversation.runtime import (
    CONVERSATION_INSTRUCTIONS,
    CONVERSATION_PROMPT_VERSION,
)
from agents_backend.conversation.tools import ToolRegistry
from agents_backend.model_gateway.client import ModelGateway

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evaluation" / "synthetic-agent-turns.jsonl"
DEFAULT_REPORT = ROOT / "evaluation" / "conversation-report.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def output_item_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return item.model_dump(mode="json", exclude_none=True)


def simulated_tool_output(tool_name: str) -> dict[str, Any]:
    if tool_name in {"delete_memory", "delete_source"}:
        return {
            "ok": True,
            "code": "confirmation_required",
            "message": "A exclusão aguarda confirmação em outra mensagem.",
            "data": {
                "id": "20000000-0000-4000-8000-000000000001",
                "status": "pending",
            },
            "evidence": [],
            "retryable": False,
        }
    if tool_name == "confirm_action":
        code, message = "action_executed", "A ação confirmada foi executada."
    elif tool_name == "cancel_action":
        code, message = "action_cancelled", "A ação pendente foi cancelada."
    elif tool_name == "get_pending_action":
        return {
            "ok": True,
            "code": "pending_action_found",
            "message": "Há uma ação aguardando confirmação.",
            "data": [
                {
                    "id": "20000000-0000-4000-8000-000000000001",
                    "summary": "Excluir o item solicitado",
                    "status": "pending",
                    "expires_at": "2026-08-16T16:00:00Z",
                }
            ],
            "evidence": [],
            "retryable": False,
        }
    elif tool_name == "remember_transcript":
        code, message = "transcript_accepted", "A transcrição foi aceita."
    elif tool_name in {"correct_memory", "dispute_memory"}:
        code, message = "memory_updated", "A memória foi atualizada."
    elif tool_name == "search_memory":
        return {
            "ok": True,
            "code": "results_found",
            "message": "A consulta retornou um fato sintético.",
            "data": {
                "items": [
                    {
                        "id": "10000000-0000-4000-8000-000000000003",
                        "type": "fact",
                        "title": "delivery_date",
                        "content": "20 de setembro",
                        "status": "current",
                    },
                    {
                        "id": "10000000-0000-4000-8000-000000000004",
                        "type": "commitment",
                        "title": "Compromisso",
                        "content": "Enviar o relatório",
                        "status": "open",
                    },
                ]
            },
            "evidence": [],
            "retryable": False,
        }
    else:
        code, message = "results_found", "A consulta retornou resultados sintéticos."
    return {
        "ok": True,
        "code": code,
        "message": message,
        "data": {"items": []},
        "evidence": [],
        "retryable": False,
    }


async def evaluate_case(
    gateway: ModelGateway, registry: ToolRegistry, case: dict[str, Any]
) -> dict[str, Any]:
    input_items: list[dict[str, Any]] = []
    if case.get("prior_assistant"):
        input_items.append({"role": "assistant", "content": case["prior_assistant"]})
    input_items.append({"role": "user", "content": case["message"]})
    calls: list[dict[str, Any]] = []
    final_text = ""
    input_tokens = 0
    output_tokens = 0
    for _ in range(4):
        response = await gateway.conversation_response(
            instructions=CONVERSATION_INSTRUCTIONS,
            input_items=input_items,
            tools=registry.definitions(),
            safety_identifier="synthetic-agent-evaluation",
        )
        usage = getattr(response, "usage", None)
        input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        output = list(getattr(response, "output", []) or [])
        function_calls = [item for item in output if getattr(item, "type", None) == "function_call"]
        if not function_calls:
            final_text = str(getattr(response, "output_text", "") or "").strip()
            break
        input_items.extend(output_item_dict(item) for item in output)
        for item in function_calls:
            name = str(getattr(item, "name", ""))
            arguments = str(getattr(item, "arguments", "{}"))
            calls.append(
                {
                    "name": name,
                    "arguments_valid": registry.validate_arguments(name, arguments),
                }
            )
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(getattr(item, "call_id", "")),
                    "output": json.dumps(simulated_tool_output(name), ensure_ascii=False),
                }
            )
    names = [call["name"] for call in calls]
    expected = case.get("expected_first_tool")
    accepted_first = set(case.get("accepted_first_tools", [expected] if expected else []))
    required_tool = case.get("required_tool", expected)
    forbidden = set(case.get("forbidden_tools", []))
    first_tool_correct = (
        bool(names) and names[0] in accepted_first if expected is not None else True
    )
    required_tool_used = required_tool is None or required_tool in names
    selection_correct = first_tool_correct and required_tool_used
    return {
        "id": case["id"],
        "expected_first_tool": expected,
        "required_tool": required_tool,
        "tool_calls": calls,
        "selection_correct": selection_correct,
        "forbidden_tool_used": any(name in forbidden for name in names),
        "arguments_valid": all(call["arguments_valid"] for call in calls),
        "feedback_present": bool(final_text),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Avalia seleção e contratos de tools do agente sem executar efeitos reais."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--enforce-gates", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    cases = load_cases(args.cases)
    if args.case_limit is not None:
        cases = cases[: args.case_limit]
    gateway = ModelGateway(settings)
    registry = ToolRegistry()
    results = [await evaluate_case(gateway, registry, case) for case in cases]
    count = len(results) or 1
    metrics = {
        "selection_accuracy": sum(result["selection_correct"] for result in results) / count,
        "valid_arguments_rate": sum(result["arguments_valid"] for result in results) / count,
        "feedback_rate": sum(result["feedback_present"] for result in results) / count,
        "forbidden_tool_uses": sum(result["forbidden_tool_used"] for result in results),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": settings.openai_model_conversation,
        "prompt_version": CONVERSATION_PROMPT_VERSION,
        "safe_simulation": True,
        "case_count": len(results),
        "metrics": metrics,
        "cases": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    gates_passed = (
        metrics["selection_accuracy"] >= 0.95
        and metrics["valid_arguments_rate"] >= 0.99
        and metrics["feedback_rate"] >= 0.95
        and metrics["forbidden_tool_uses"] == 0
    )
    return 1 if args.enforce_gates and not gates_passed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
