from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from agents_backend.api.dependencies import SessionDependency
from agents_backend.config import get_settings
from agents_backend.errors import AppError
from agents_backend.integrations.macwhisper.service import (
    MacWhisperWebhookPayload,
    ingest_webhook,
)

router = APIRouter()


async def _bounded_body(request: Request, maximum_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise AppError("invalid_webhook", "Content-Length inválido.", 400) from exc
        if declared_length > maximum_bytes:
            raise AppError("payload_too_large", "Payload do webhook muito grande.", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise AppError("payload_too_large", "Payload do webhook muito grande.", 413)
    return bytes(body)


@router.post(
    "/v1/integrations/macwhisper/webhooks/{token}",
    include_in_schema=False,
)
async def receive_macwhisper_webhook(
    token: str,
    request: Request,
    session: SessionDependency,
) -> JSONResponse:
    settings = get_settings()
    if not settings.macwhisper_webhook_enabled:
        return JSONResponse({"status": "not_found"}, status_code=404)
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,100}", token):
        return JSONResponse({"status": "not_found"}, status_code=404)
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise AppError("invalid_webhook", "O webhook deve enviar application/json.", 415)
    body = await _bounded_body(request, settings.macwhisper_max_payload_bytes)
    try:
        raw: Any = json.loads(body)
        payload = MacWhisperWebhookPayload.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise AppError("invalid_webhook", "Payload MacWhisper inválido.", 400) from exc
    result = await ingest_webhook(session, token, payload, settings)
    return JSONResponse(
        {
            "status": "accepted",
            "source_id": str(result.source_id),
            "idempotent_replay": result.idempotent_replay,
        },
        status_code=200 if result.idempotent_replay else 202,
        headers={"Cache-Control": "no-store"},
    )
