from __future__ import annotations

import json
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from agents_backend.config import Settings


class Bitrix24AuthenticationError(RuntimeError):
    pass


class Bitrix24ToolError(RuntimeError):
    pass


def _message(payload: dict[str, Any]) -> str:
    messages: list[str] = []
    for item in payload.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        raw = str(item.get("text", "")).strip()
        if not raw:
            continue
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            messages.append(raw)
            continue
        if isinstance(decoded, dict):
            value = decoded.get("message") or decoded.get("error") or decoded.get("detail")
            if value:
                messages.append(str(value))
    joined = " ".join(messages) or "O Bitrix24 rejeitou a operação."
    return joined[:1000]


class Bitrix24Gateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self, token: str) -> dict[str, str]:
        value = f"Bearer {token}" if self.settings.bitrix24_auth_scheme == "bearer" else token
        return {"Authorization": value}

    async def list_tools(self, token: str) -> list[dict[str, Any]]:
        timeout = httpx.Timeout(self.settings.bitrix24_timeout_seconds)
        try:
            async with httpx.AsyncClient(headers=self._headers(token), timeout=timeout) as client:
                async with streamable_http_client(
                    self.settings.bitrix24_mcp_url,
                    http_client=client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise Bitrix24AuthenticationError("Token de conexão recusado") from exc
            raise
        return [tool.model_dump(mode="json", exclude_none=True) for tool in tools]

    async def execute(
        self, token: str, remote_tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(self.settings.bitrix24_timeout_seconds)
        try:
            async with httpx.AsyncClient(headers=self._headers(token), timeout=timeout) as client:
                async with streamable_http_client(
                    self.settings.bitrix24_mcp_url,
                    http_client=client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        available = {tool.name for tool in (await session.list_tools()).tools}
                        if remote_tool not in available:
                            raise Bitrix24ToolError(
                                "A tool configurada não está disponível para este usuário"
                            )
                        result = await session.call_tool(remote_tool, arguments)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise Bitrix24AuthenticationError(
                    "A conexão Bitrix24 perdeu a autorização"
                ) from exc
            raise
        payload = result.model_dump(mode="json", exclude_none=True)
        if result.isError:
            raise Bitrix24ToolError(_message(payload))
        return payload
