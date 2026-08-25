from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Autenticação necessária.") -> None:
        super().__init__("unauthorized", message, 401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Você não possui permissão para esta operação.") -> None:
        super().__init__("forbidden", message, 403)


class NotFoundError(AppError):
    def __init__(self, message: str = "Recurso não encontrado.") -> None:
        super().__init__("not_found", message, 404)


class ConflictError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 409)
