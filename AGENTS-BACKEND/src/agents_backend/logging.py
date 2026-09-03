from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_context: ContextVar[str | None] = ContextVar("job_id", default=None)


class SensitiveRequestPathFilter(logging.Filter):
    _pattern = re.compile(r"(/v1/integrations/macwhisper/webhooks/)[A-Za-z0-9_-]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access" or not isinstance(record.args, tuple):
            return True
        values = list(record.args)
        if len(values) >= 3 and isinstance(values[2], str):
            values[2] = self._pattern.sub(r"\1[redacted]", values[2])
            record.args = tuple(values)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
            "job_id": job_id_context.get(),
        }
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        for key in (
            "worker_stage",
            "worker_id",
            "deployment_revision",
            "error_type",
            "error_message",
            "consecutive_failures",
            "queue_lag_seconds",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # INFO request logs may contain Telegram tokens in URL paths and hosted MCP credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, SensitiveRequestPathFilter) for item in access_logger.filters):
        access_logger.addFilter(SensitiveRequestPathFilter())
