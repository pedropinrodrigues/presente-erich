from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from agents_backend.config import Settings, get_settings


class AssemblyAIError(RuntimeError):
    def __init__(self, code: str, *, transient: bool, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.transient = transient


@dataclass(frozen=True, slots=True)
class AssemblyAITranscript:
    transcript_id: str
    status: str
    text: str | None = None
    confidence: float | None = None
    audio_duration: float | None = None
    language_code: str | None = None
    speech_model_used: str | None = None
    error: str | None = None


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value)


class AssemblyAIClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client

    @property
    def _headers(self) -> dict[str, str]:
        token = _secret_value(self.settings.assembly_ai_api_token)
        if not token:
            raise AssemblyAIError("assemblyai_not_configured", transient=False)
        return {"authorization": token}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.settings.assemblyai_api_base_url.rstrip('/')}{path}"
        supplied_headers = kwargs.pop("headers", {})
        headers = {**self._headers, **supplied_headers}
        try:
            if self.client is not None:
                response = await self.client.request(
                    method,
                    url,
                    headers=headers,
                    **kwargs,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.settings.assemblyai_timeout_seconds
                ) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=headers,
                        **kwargs,
                    )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AssemblyAIError("assemblyai_unavailable", transient=True) from exc
        if response.is_success:
            return response
        transient = response.status_code == 429 or response.status_code >= 500
        raise AssemblyAIError(
            f"assemblyai_http_{response.status_code}",
            transient=transient,
        )

    async def upload_audio(self, audio: bytes) -> str:
        response = await self._request(
            "POST",
            "/v2/upload",
            content=audio,
            headers={**self._headers, "content-type": "application/octet-stream"},
        )
        value = response.json().get("upload_url")
        if not isinstance(value, str) or not value:
            raise AssemblyAIError("assemblyai_invalid_upload_response", transient=True)
        return value

    async def submit_transcript(self, upload_url: str) -> str:
        response = await self._request(
            "POST",
            "/v2/transcript",
            json={
                "audio_url": upload_url,
                "speech_models": [self.settings.assemblyai_model],
                "language_code": self.settings.assemblyai_language_code,
            },
        )
        value = response.json().get("id")
        if not isinstance(value, str) or not value:
            raise AssemblyAIError("assemblyai_invalid_submit_response", transient=True)
        return value

    async def get_transcript(self, transcript_id: str) -> AssemblyAITranscript:
        response = await self._request("GET", f"/v2/transcript/{transcript_id}")
        value = response.json()
        return AssemblyAITranscript(
            transcript_id=str(value.get("id") or transcript_id),
            status=str(value.get("status") or "unknown"),
            text=value.get("text") if isinstance(value.get("text"), str) else None,
            confidence=(
                float(value["confidence"])
                if isinstance(value.get("confidence"), int | float)
                else None
            ),
            audio_duration=(
                float(value["audio_duration"])
                if isinstance(value.get("audio_duration"), int | float)
                else None
            ),
            language_code=(
                value.get("language_code")
                if isinstance(value.get("language_code"), str)
                else None
            ),
            speech_model_used=(
                value.get("speech_model_used")
                if isinstance(value.get("speech_model_used"), str)
                else None
            ),
            error=value.get("error") if isinstance(value.get("error"), str) else None,
        )

    async def delete_transcript(self, transcript_id: str) -> None:
        await self._request("DELETE", f"/v2/transcript/{transcript_id}")
