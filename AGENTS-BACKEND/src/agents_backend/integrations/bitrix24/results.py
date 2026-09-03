from __future__ import annotations

import json
from typing import Any

SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "connection_token",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def _redact_and_bound(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 50:
                result["_truncated"] = True
                break
            if key.casefold() in SENSITIVE_KEYS:
                result[key] = "[redacted]"
            else:
                result[key] = _redact_and_bound(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_redact_and_bound(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:3999] + "…"
    return value


def normalize_result(payload: dict[str, Any]) -> dict[str, Any]:
    decoded: list[Any] = []
    for item in payload.get("content", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = str(item.get("text", ""))
        try:
            decoded.append(json.loads(text))
        except json.JSONDecodeError:
            decoded.append(text)
    value: Any
    if len(decoded) == 1:
        value = decoded[0]
    elif decoded:
        value = decoded
    else:
        value = {"completed": True}
    bounded = _redact_and_bound(value)
    return bounded if isinstance(bounded, dict) else {"result": bounded}
