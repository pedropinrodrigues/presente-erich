from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agents_backend.api.invitation_routes import router as invitation_router
from agents_backend.api.routes import router
from agents_backend.api.telegram_routes import router as telegram_router
from agents_backend.api.whatsapp_routes import router as whatsapp_router
from agents_backend.config import get_settings
from agents_backend.db import database_ready
from agents_backend.errors import AppError
from agents_backend.logging import configure_logging, request_id_context

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("api_started")
    yield
    logger.info("api_stopped")


def create_app() -> FastAPI:
    application = FastAPI(title="Agents & Backend", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def request_context_middleware(request: Request, call_next: object) -> JSONResponse:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)  # type: ignore[operator]
            response.headers["x-request-id"] = request_id
            return response
        finally:
            request_id_context.reset(token)

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id_context.get(),
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, _: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "A requisição não corresponde ao contrato esperado.",
                    "request_id": request_id_context.get(),
                }
            },
        )

    @application.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        ready_state = await database_ready()
        return JSONResponse(
            status_code=200 if ready_state else 503,
            content={"status": "ready" if ready_state else "not_ready"},
        )

    application.include_router(router)
    application.include_router(invitation_router)
    application.include_router(telegram_router)
    application.include_router(whatsapp_router)
    return application


app = create_app()


def run() -> None:
    uvicorn.run("agents_backend.api.main:app", host="127.0.0.1", port=8000, reload=False)
