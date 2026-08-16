from __future__ import annotations

import asyncio
import logging

from agents_backend.config import get_settings
from agents_backend.db import get_session_factory
from agents_backend.logging import configure_logging
from agents_backend.model_gateway.client import ModelGateway
from agents_backend.worker.service import claim_job, default_worker_id, process_job

logger = logging.getLogger(__name__)


async def worker_loop() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker_id = default_worker_id()
    gateway = ModelGateway(settings)
    logger.info("worker_started")
    while True:
        async with get_session_factory()() as session:
            job = await claim_job(session, worker_id)
            if job is not None:
                await process_job(session, job, gateway)
                continue
        await asyncio.sleep(settings.worker_poll_interval_seconds)


def run() -> None:
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("worker_stopped")


if __name__ == "__main__":
    run()
