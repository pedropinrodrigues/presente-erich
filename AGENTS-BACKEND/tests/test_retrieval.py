from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.retrieval.service import ask_memory
from agents_backend.schemas import AskMemoryRequest


@pytest.mark.asyncio
async def test_question_without_evidence_returns_uncertainty(
    session: AsyncSession, context: RequestContext
) -> None:
    result = await ask_memory(
        session,
        context,
        AskMemoryRequest(question="Qual é a data de renovação do contrato?"),
    )
    assert result.evidence == []
    assert result.uncertainties
    assert "Não encontrei evidência" in result.answer
