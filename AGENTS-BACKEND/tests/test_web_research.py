from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_records, conversation_settings

from agents_backend.auth import RequestContext
from agents_backend.conversation.tools import ToolRegistry, orchestration_tool_specs
from agents_backend.model_gateway.client import (
    GatewayResult,
    ModelGateway,
    WebResearchAnswer,
    WebResearchSource,
)
from agents_backend.models import ModelRun, ToolExecution


class FakeWebResponses:
    def __init__(self, *, url: str = "https://example.com/noticia") -> None:
        self.calls: list[dict[str, Any]] = []
        self.url = url

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        annotation = SimpleNamespace(
            type="url_citation",
            title="Notícia de exemplo",
            url=self.url,
        )
        return SimpleNamespace(
            id="web-response-1",
            output_text="A informação foi confirmada pela fonte consultada.",
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(annotations=[annotation])],
                )
            ],
            usage=SimpleNamespace(input_tokens=100, output_tokens=40),
        )


@pytest.mark.asyncio
async def test_gateway_uses_hosted_web_search_with_limits_and_citations() -> None:
    responses = FakeWebResponses()
    settings = conversation_settings()
    gateway = ModelGateway(
        settings=settings,
        client=SimpleNamespace(responses=responses),  # type: ignore[arg-type]
    )

    result = await gateway.research_web(
        query="Qual é a notícia mais recente?",
        allowed_domains=["example.com"],
        safety_identifier="safe-user",
    )

    request = responses.calls[0]
    assert request["store"] is False
    assert request["tool_choice"] == "required"
    assert request["max_tool_calls"] == settings.web_research_max_tool_calls
    assert request["include"] == ["web_search_call.action.sources"]
    assert request["tools"][0] == {
        "type": "web_search",
        "search_context_size": "medium",
        "user_location": {
            "type": "approximate",
            "country": "BR",
            "timezone": "America/Sao_Paulo",
        },
        "filters": {"allowed_domains": ["example.com"]},
    }
    assert isinstance(result.value, WebResearchAnswer)
    assert result.value.sources[0].url == "https://example.com/noticia"


@pytest.mark.asyncio
async def test_gateway_discards_unsafe_citation_urls() -> None:
    responses = FakeWebResponses(url="file:///etc/passwd")
    gateway = ModelGateway(
        settings=conversation_settings(),
        client=SimpleNamespace(responses=responses),  # type: ignore[arg-type]
    )

    result = await gateway.research_web(
        query="Pesquise um fato atual",
        allowed_domains=None,
        safety_identifier="safe-user",
    )

    assert result.value.sources == []


class FakeResearchGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def research_web(self, **_: Any) -> GatewayResult:
        self.calls += 1
        return GatewayResult(
            value=WebResearchAnswer(
                answer="Resposta fundamentada.",
                sources=[
                    WebResearchSource(title="Fonte confiável", url="https://example.com/fato")
                ],
                searched_at=datetime.now(UTC),
            ),
            provider_request_id="web-1",
            model="gpt-5.6-terra",
            prompt_version="web-research-test",
            schema_version="web-research-v1",
            duration_ms=25,
            input_tokens=20,
            output_tokens=10,
        )


@pytest.mark.asyncio
async def test_research_tool_is_grounded_audited_redacted_and_idempotent(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    conversation, message, run = await conversation_records(
        session,
        context,
        content="Pesquise a informação mais recente.",
    )
    registry = ToolRegistry(orchestration_tool_specs(["web_research"]))
    gateway = FakeResearchGateway()
    kwargs = {
        "session": session,
        "request_context": context,
        "conversation": conversation,
        "inbound_message": message,
        "agent_run": run,
        "call_id": "research-1",
        "tool_name": "research_web",
        "raw_arguments": json.dumps({"query": "fato atual", "allowed_domains": ["example.com"]}),
        "settings": conversation_settings(),
        "model_gateway": gateway,
    }

    first = await registry.execute(**kwargs)  # type: ignore[arg-type]
    second = await registry.execute(**kwargs)  # type: ignore[arg-type]

    execution = await session.scalar(select(ToolExecution))
    model_run = await session.scalar(select(ModelRun))
    assert first.envelope.ok is True
    assert first.envelope.data["source_count"] == 1  # type: ignore[index]
    assert second.replayed is True
    assert gateway.calls == 1
    assert execution is not None
    assert execution.sanitized_arguments["query"]["redacted"] is True
    assert model_run is not None and model_run.success is True


def test_research_tool_rejects_urls_in_domain_filter() -> None:
    registry = ToolRegistry(orchestration_tool_specs(["web_research"]))

    assert registry.validate_arguments(
        "research_web",
        json.dumps({"query": "fato atual", "allowed_domains": ["example.com"]}),
    )
    assert not registry.validate_arguments(
        "research_web",
        json.dumps({"query": "fato atual", "allowed_domains": ["https://example.com/path"]}),
    )
