from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from agents_backend.api.dependencies import (
    ContextDependency,
    ConversationServiceDependency,
    SessionDependency,
)
from agents_backend.config import get_settings
from agents_backend.conversation.whatsapp import (
    bind_whatsapp_account,
    ingest_webhook_payload,
    verify_webhook_signature,
    verify_webhook_token,
)
from agents_backend.errors import AppError, UnauthorizedError
from agents_backend.schemas import WhatsappAccountRequest, WhatsappAccountResponse

router = APIRouter()


@router.get("/webhooks/whatsapp", include_in_schema=False)
async def verify_whatsapp_webhook(
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    if not challenge or not verify_webhook_token(mode, token, get_settings()):
        raise UnauthorizedError("Verificação do webhook inválida.")
    return PlainTextResponse(challenge)


@router.post("/webhooks/whatsapp", include_in_schema=False)
async def receive_whatsapp_webhook(
    request: Request,
    session: SessionDependency,
    conversation_service: ConversationServiceDependency,
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> JSONResponse:
    body = await request.body()
    if not verify_webhook_signature(body, signature, get_settings()):
        raise UnauthorizedError("Assinatura do webhook inválida.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError("invalid_webhook", "Payload de webhook inválido.", 400) from exc
    if not isinstance(payload, dict):
        raise AppError("invalid_webhook", "Payload de webhook inválido.", 400)
    accepted, duplicates = await ingest_webhook_payload(session, payload, conversation_service)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "messages": accepted, "duplicates": duplicates},
    )


@router.post(
    "/v1/channels/whatsapp/accounts",
    response_model=WhatsappAccountResponse,
    status_code=201,
)
async def post_whatsapp_account(
    payload: WhatsappAccountRequest,
    session: SessionDependency,
    context: ContextDependency,
) -> WhatsappAccountResponse:
    return await bind_whatsapp_account(session, context, payload)
