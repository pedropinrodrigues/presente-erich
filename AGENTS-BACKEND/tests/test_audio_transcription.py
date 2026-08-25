from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_conversation_tools import conversation_settings
from test_telegram import telegram_update, telegram_voice_update

from agents_backend.auth import RequestContext
from agents_backend.conversation.channel_jobs import claim_channel_message
from agents_backend.conversation.service import ConversationService
from agents_backend.conversation.telegram import bind_telegram_account, ingest_telegram_update
from agents_backend.models import AudioTranscriptionJob, ChannelMessage
from agents_backend.schemas import TelegramAccountRequest
from agents_backend.transcription.assemblyai import AssemblyAIClient, AssemblyAITranscript
from agents_backend.transcription.service import process_audio_transcription_job


class FakeTelegramAudioClient:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    async def download_file(self, file_id: str, maximum_bytes: int) -> bytes:
        assert file_id == "telegram-file"
        assert maximum_bytes >= 5
        return b"audio"

    async def send_chat_action(self, destination: str, action: str = "typing") -> None:
        self.actions.append((destination, action))


class FakeAssemblyAIClient:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def upload_audio(self, audio: bytes) -> str:
        assert audio == b"audio"
        return "https://assembly.example/upload"

    async def submit_transcript(self, upload_url: str) -> str:
        assert upload_url == "https://assembly.example/upload"
        return "transcript-1"

    async def get_transcript(self, transcript_id: str) -> AssemblyAITranscript:
        return AssemblyAITranscript(
            transcript_id=transcript_id,
            status="completed",
            text="Marque uma conversa com Ana amanhã.",
            confidence=0.91,
            language_code="pt",
            speech_model_used="universal-2",
        )

    async def delete_transcript(self, transcript_id: str) -> None:
        self.deleted.append(transcript_id)


@pytest.mark.asyncio
async def test_assemblyai_client_submits_universal_2() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v2/upload":
            return httpx.Response(200, json={"upload_url": "https://upload.example/audio"})
        if request.url.path == "/v2/transcript" and request.method == "POST":
            assert request.read()
            return httpx.Response(200, json={"id": "transcript-1"})
        return httpx.Response(200, json={"id": "transcript-1", "status": "completed"})

    settings = conversation_settings(
        ASSEMBLY_AI_API_TOKEN="assembly-token",  # noqa: S106
        ASSEMBLYAI_MODEL="universal-2",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assemblyai = AssemblyAIClient(settings, client)
        upload_url = await assemblyai.upload_audio(b"audio")
        transcript_id = await assemblyai.submit_transcript(upload_url)

    assert transcript_id == "transcript-1"
    assert requests[0].headers["authorization"] == "assembly-token"
    assert b'"speech_models":["universal-2"]' in requests[1].content


@pytest.mark.asyncio
async def test_voice_job_becomes_a_normal_inbound_message(
    session: AsyncSession,
    context: RequestContext,
) -> None:
    settings = conversation_settings(
        TELEGRAM_BOT_TOKEN="bot-token",  # noqa: S106
        TELEGRAM_BOT_USERNAME="test_agent_bot",
        ASSEMBLY_AI_API_TOKEN="assembly-token",  # noqa: S106
    )
    binding = await bind_telegram_account(
        session, context, TelegramAccountRequest(display_name="Pedro"), settings
    )
    code = str(binding.verification_deep_link).split("start=", maxsplit=1)[1]
    service = ConversationService(settings=settings)
    await ingest_telegram_update(
        session, telegram_update(text=f"/start {code}", message_id=1), service
    )
    await ingest_telegram_update(session, telegram_voice_update(message_id=2), service)
    await ingest_telegram_update(
        session, telegram_update(text="E depois me confirme.", message_id=3), service
    )
    job = await session.scalar(select(AudioTranscriptionJob))
    assert job is not None
    job_id = job.id
    assert await claim_channel_message(session, "worker-1", ("telegram",)) is None
    job = await session.get(AudioTranscriptionJob, job_id)
    assert job is not None
    telegram = FakeTelegramAudioClient()
    assemblyai = FakeAssemblyAIClient()

    for _ in range(3):
        await process_audio_transcription_job(
            session,
            job,
            settings=settings,
            telegram=telegram,
            assemblyai=assemblyai,  # type: ignore[arg-type]
        )

    inbound = await session.get(ChannelMessage, job.channel_message_id)
    assert inbound is not None
    assert inbound.status == "received"
    assert inbound.content == "Marque uma conversa com Ana amanhã."
    assert inbound.message_metadata["transcription_model"] == "universal-2"
    assert inbound.message_metadata["transcription_low_confidence"] is False
    assert job.status == "completed"
    assert telegram.actions == [("123456789", "typing")]
    assert assemblyai.deleted == ["transcript-1"]
    claimed = await claim_channel_message(session, "worker-1", ("telegram",))
    assert claimed is not None
    assert claimed.id == inbound.id
