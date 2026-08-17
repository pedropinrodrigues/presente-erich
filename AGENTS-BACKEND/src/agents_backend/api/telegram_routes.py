from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from agents_backend.api.dependencies import (
    ContextDependency,
    ConversationServiceDependency,
    SessionDependency,
)
from agents_backend.config import get_settings
from agents_backend.conversation.telegram import (
    bind_telegram_account,
    ingest_telegram_update,
    verify_telegram_webhook_secret,
)
from agents_backend.errors import AppError, UnauthorizedError
from agents_backend.schemas import TelegramAccountRequest, TelegramAccountResponse

router = APIRouter()


@router.post("/webhooks/telegram", include_in_schema=False)
async def receive_telegram_webhook(
    request: Request,
    session: SessionDependency,
    conversation_service: ConversationServiceDependency,
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> JSONResponse:
    if not verify_telegram_webhook_secret(secret, get_settings()):
        raise UnauthorizedError("Segredo do webhook do Telegram inválido.")
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError("invalid_webhook", "Payload de webhook inválido.", 400) from exc
    if not isinstance(payload, dict):
        raise AppError("invalid_webhook", "Payload de webhook inválido.", 400)
    accepted, duplicates = await ingest_telegram_update(session, payload, conversation_service)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "messages": accepted, "duplicates": duplicates},
    )


@router.post(
    "/v1/channels/telegram/accounts",
    response_model=TelegramAccountResponse,
    status_code=201,
)
async def post_telegram_account(
    payload: TelegramAccountRequest,
    session: SessionDependency,
    context: ContextDependency,
) -> TelegramAccountResponse:
    return await bind_telegram_account(session, context, payload)
