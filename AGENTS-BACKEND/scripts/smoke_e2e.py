from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select

from agents_backend.auth import Identity, RequestContext
from agents_backend.db import get_session_factory
from agents_backend.ingestion.service import ingest_transcript
from agents_backend.memory.mutations import correct_memory, delete_source
from agents_backend.model_gateway.client import ModelGateway
from agents_backend.models import Evidence, Fact, Job, Source, Workspace
from agents_backend.retrieval.service import ask_memory, search_memory
from agents_backend.schemas import AskMemoryRequest, CorrectionRequest, TranscriptEvent
from agents_backend.worker.service import process_job


async def smoke_test() -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    context = RequestContext(identity=Identity(user_id=user_id), workspace_id=workspace_id)
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            session.add(Workspace(id=workspace_id, owner_user_id=user_id))
            await session.commit()
            ingest_response, status = await ingest_transcript(
                session,
                context,
                TranscriptEvent(
                    capture_id=uuid.uuid4(),
                    source="synthetic-smoke",
                    captured_at=datetime.now(UTC),
                    transcript=(
                        "Marina decidiu que o Projeto Atlas será lançado "
                        "em 20 de setembro de 2026. "
                        "Paulo ficou responsável por revisar a página até 8 de setembro de 2026."
                    ),
                    language="pt-BR",
                    metadata={"smoke_test": True},
                ),
            )
            assert status == 201
            job = await session.scalar(
                select(Job).where(
                    Job.workspace_id == workspace_id,
                    Job.source_id == ingest_response.source_id,
                )
            )
            assert job is not None
            job.status = "running"
            job.attempts = 1
            await session.commit()
            await process_job(session, job, ModelGateway())

            source = await session.get(Source, ingest_response.source_id)
            assert source is not None and source.status == "processed"
            fact_count = await session.scalar(
                select(func.count(Fact.id)).where(Fact.workspace_id == workspace_id)
            )
            evidence_count = await session.scalar(
                select(func.count(Evidence.id)).where(Evidence.workspace_id == workspace_id)
            )
            assert fact_count and fact_count > 0
            assert evidence_count and evidence_count > 0

            semantic_search = await search_memory(
                session,
                context,
                query="calendário previsto para disponibilizar o Atlas",
                entity_id=None,
                item_type="fact",
                status="current",
                from_=None,
                to=None,
                cursor=None,
            )
            assert semantic_search.items

            answer = await ask_memory(
                session,
                context,
                AskMemoryRequest(question="Quando o Projeto Atlas será lançado?"),
            )
            assert answer.evidence
            assert answer.source_ids == [source.id]

            fact = await session.scalar(
                select(Fact).where(
                    Fact.workspace_id == workspace_id,
                    Fact.status == "current",
                )
            )
            assert fact is not None
            correction = await correct_memory(
                session,
                context,
                CorrectionRequest(
                    target_id=fact.id,
                    target_type="fact",
                    operation="dispute",
                    reason="Validação sintética do fluxo de correção.",
                ),
            )
            assert correction.status == "disputed"
            deletion = await delete_source(
                session,
                context,
                source.id,
                "Limpeza do smoke test sintético.",
            )
            assert deletion.status == "deleted"
            assert source.transcript is None
            print(
                "smoke_e2e_ok",
                f"facts={fact_count}",
                f"evidence={evidence_count}",
                f"semantic_items={len(semantic_search.items)}",
                f"answer_evidence={len(answer.evidence)}",
            )
    finally:
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await cleanup_session.commit()


if __name__ == "__main__":
    asyncio.run(smoke_test())
