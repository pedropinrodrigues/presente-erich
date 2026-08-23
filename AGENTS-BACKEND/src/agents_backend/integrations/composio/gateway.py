from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from composio import SESSION_PRESET_DIRECT_TOOLS, Composio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from agents_backend.config import Settings


class ComposioToolExecutionError(RuntimeError):
    """The remote tool ran but rejected the request or returned a provider error."""


def _remote_error_message(payload: dict[str, Any]) -> str:
    candidates: list[str] = []
    for item in payload.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            candidates.append(text)
            continue
        if isinstance(decoded, dict):
            for key in ("error", "message"):
                value = decoded.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
                elif isinstance(value, dict):
                    nested = value.get("message") or value.get("detail")
                    if isinstance(nested, str) and nested.strip():
                        candidates.append(nested.strip())
    if not candidates:
        return "O provedor rejeitou a solicitação sem informar um motivo."
    message = " ".join(candidates)
    return message if len(message) <= 1000 else message[:999].rstrip() + "…"


class ComposioGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = Composio(api_key=settings.composio_api_key.get_secret_value())

    async def create_link(
        self,
        *,
        user_id: str,
        auth_config_id: str,
        callback_url: str,
        alias: str | None = None,
        allow_multiple: bool = False,
    ) -> Any:
        return await asyncio.to_thread(
            self.client.connected_accounts.link,
            user_id,
            auth_config_id,
            callback_url=callback_url,
            alias=alias,
            allow_multiple=allow_multiple,
        )

    async def get_connected_account(self, connected_account_id: str) -> Any:
        return await asyncio.to_thread(
            self.client.connected_accounts.get,
            connected_account_id,
        )

    async def execute(
        self,
        *,
        user_id: str,
        toolkit: str,
        auth_config_id: str,
        connected_account_id: str,
        remote_slug: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        router = await asyncio.to_thread(
            self.client.sessions.create,
            user_id=user_id,
            toolkits=[toolkit],
            tools={toolkit: {"enable": [remote_slug]}},
            manage_connections=False,
            auth_configs={toolkit: auth_config_id},
            connected_accounts={toolkit: connected_account_id},
            sandbox={"enable": False},
            session_preset=SESSION_PRESET_DIRECT_TOOLS,
            mcp=True,
        )
        endpoint = router.mcp
        timeout = httpx.Timeout(self.settings.composio_timeout_seconds)
        async with httpx.AsyncClient(headers=endpoint.headers, timeout=timeout) as http_client:
            async with streamable_http_client(
                endpoint.url, http_client=http_client, terminate_on_close=False
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    available = {tool.name for tool in (await session.list_tools()).tools}
                    if remote_slug not in available:
                        raise RuntimeError("Composio não expôs a tool permitida nesta sessão")
                    result = await session.call_tool(remote_slug, arguments)
        payload = result.model_dump(mode="json", exclude_none=True)
        if result.isError:
            raise ComposioToolExecutionError(_remote_error_message(payload))
        return payload, str(router.session_id)
