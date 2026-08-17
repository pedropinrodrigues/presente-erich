from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response

from agents_backend.api.dependencies import (
    ContextDependency,
    ConversationServiceDependency,
    SessionDependency,
)
from agents_backend.ingestion.service import get_source, ingest_transcript
from agents_backend.memory.mutations import (
    correct_memory,
    delete_memory_target,
    delete_source,
)
from agents_backend.retrieval.service import ask_memory, get_entity_view, search_memory
from agents_backend.schemas import (
    AgentTurnRequest,
    AgentTurnResponse,
    AskMemoryRequest,
    AskMemoryResponse,
    CorrectionRequest,
    EntityResponse,
    IngestTranscriptResponse,
    MutationResponse,
    SearchMemoryResponse,
    SourceResponse,
    TranscriptEvent,
)

router = APIRouter(prefix="/v1")


@router.post("/agent/turns", response_model=AgentTurnResponse)
async def post_agent_turn(
    payload: AgentTurnRequest,
    session: SessionDependency,
    context: ContextDependency,
    conversation_service: ConversationServiceDependency,
) -> AgentTurnResponse:
    return await conversation_service.process_api_turn(session, context, payload)


@router.post(
    "/transcripts",
    response_model=IngestTranscriptResponse,
    status_code=201,
    responses={200: {"model": IngestTranscriptResponse}, 409: {"description": "Conflito"}},
)
async def post_transcript(
    payload: TranscriptEvent,
    response: Response,
    session: SessionDependency,
    context: ContextDependency,
) -> IngestTranscriptResponse:
    result, status_code = await ingest_transcript(session, context, payload)
    response.status_code = status_code
    return result


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def read_source(
    source_id: uuid.UUID,
    session: SessionDependency,
    context: ContextDependency,
) -> SourceResponse:
    return await get_source(session, context, source_id)


@router.delete("/sources/{source_id}", response_model=MutationResponse)
async def remove_source(
    source_id: uuid.UUID,
    session: SessionDependency,
    context: ContextDependency,
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> MutationResponse:
    return await delete_source(session, context, source_id, reason)


@router.post("/memory/ask", response_model=AskMemoryResponse)
async def post_ask_memory(
    payload: AskMemoryRequest,
    session: SessionDependency,
    context: ContextDependency,
) -> AskMemoryResponse:
    return await ask_memory(session, context, payload)


@router.get("/memory/search", response_model=SearchMemoryResponse)
async def get_memory_search(
    session: SessionDependency,
    context: ContextDependency,
    q: str | None = Query(default=None, max_length=500),
    entity_id: uuid.UUID | None = None,
    type: str | None = Query(default=None, pattern="^(entity|fact|commitment)$"),
    status: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> SearchMemoryResponse:
    return await search_memory(
        session,
        context,
        query=q,
        entity_id=entity_id,
        item_type=type,
        status=status,
        from_=from_,
        to=to,
        cursor=cursor,
        limit=limit,
    )


@router.get("/entities/{entity_id}", response_model=EntityResponse)
async def read_entity(
    entity_id: uuid.UUID,
    session: SessionDependency,
    context: ContextDependency,
) -> EntityResponse:
    return await get_entity_view(session, context, entity_id)


@router.post("/memory/corrections", response_model=MutationResponse)
async def post_memory_correction(
    payload: CorrectionRequest,
    session: SessionDependency,
    context: ContextDependency,
) -> MutationResponse:
    return await correct_memory(session, context, payload)


@router.delete("/memory/{target_id}", response_model=MutationResponse)
async def remove_memory(
    target_id: uuid.UUID,
    session: SessionDependency,
    context: ContextDependency,
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> MutationResponse:
    return await delete_memory_target(session, context, target_id, reason)
