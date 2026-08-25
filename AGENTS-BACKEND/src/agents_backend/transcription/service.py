from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.config import Settings
from agents_backend.models import (
    AudioTranscriptionJob,
    ChannelAccount,
    ChannelMessage,
    Conversation,
    OutboxMessage,
)

from .assemblyai import AssemblyAIClient, AssemblyAIError

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TelegramAudioClient(Protocol):
    async def download_file(self, file_id: str, maximum_bytes: int) -> bytes: ...

    async def send_chat_action(self, destination: str, action: str = "typing") -> None: ...


class AudioTranscriptionError(RuntimeError):
    def __init__(self, code: str, *, transient: bool, user_message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.user_message = user_message


async def claim_audio_transcription_job(
    session: AsyncSession,
    worker_id: str,
) -> AudioTranscriptionJob | None:
    now = datetime.now(UTC)
    statement = (
        select(AudioTranscriptionJob)
        .where(
            or_(
                (
                    AudioTranscriptionJob.status.in_(["queued", "retrying"])
                    & (AudioTranscriptionJob.available_at <= now)
                ),
                (
                    (AudioTranscriptionJob.status == "running")
                    & (AudioTranscriptionJob.lease_expires_at < now)
                ),
            )
        )
        .order_by(AudioTranscriptionJob.available_at, AudioTranscriptionJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = await session.scalar(statement)
    if job is None:
        await session.rollback()
        return None
    job.status = "running"
    job.locked_by = worker_id
    job.lease_expires_at = now + timedelta(minutes=2)
    job.error_code = None
    job.error_detail = None
    await session.commit()
    return job


def _release(job: AudioTranscriptionJob, *, delay_seconds: float = 0) -> None:
    job.status = "queued"
    job.available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
    job.locked_by = None
    job.lease_expires_at = None


async def _failure_destination(
    session: AsyncSession,
    conversation: Conversation,
    inbound: ChannelMessage,
) -> str:
    if conversation.channel_account_id is not None:
        account = await session.get(ChannelAccount, conversation.channel_account_id)
        if account is not None:
            return account.external_account_id
    return str(inbound.message_metadata.get("sender") or "")


async def _persist_terminal_failure(
    session: AsyncSession,
    job: AudioTranscriptionJob,
    *,
    code: str,
    user_message: str,
) -> None:
    inbound = await session.get(ChannelMessage, job.channel_message_id)
    conversation = await session.get(Conversation, job.conversation_id)
    now = datetime.now(UTC)
    job.status = "failed"
    job.error_code = code
    job.error_detail = "A transcrição não pôde ser concluída com segurança."
    job.locked_by = None
    job.lease_expires_at = None
    job.completed_at = now
    if inbound is None or conversation is None:
        await session.commit()
        return
    inbound.status = "failed"
    inbound.error_code = code
    inbound.locked_by = None
    inbound.lease_expires_at = None
    key = f"audio-transcription-failure:{job.id}"
    existing = await session.scalar(
        select(OutboxMessage).where(OutboxMessage.idempotency_key == key)
    )
    if existing is None:
        outbound = ChannelMessage(
            workspace_id=job.workspace_id,
            conversation_id=job.conversation_id,
            reply_to_message_id=inbound.id,
            provider=conversation.provider,
            direction="outbound",
            content=user_message,
            status="queued",
            message_metadata={"response_phase": "transcription_error"},
        )
        session.add(outbound)
        await session.flush()
        session.add(
            OutboxMessage(
                workspace_id=job.workspace_id,
                conversation_id=job.conversation_id,
                channel_message_id=outbound.id,
                provider=conversation.provider,
                destination=await _failure_destination(session, conversation, inbound),
                payload={"type": "text", "text": {"body": user_message}},
                status="pending",
                idempotency_key=key,
            )
        )
    await session.commit()


async def _handle_failure(
    session: AsyncSession,
    job_id: uuid.UUID,
    error: Exception,
) -> None:
    await session.rollback()
    job = await session.get(AudioTranscriptionJob, job_id)
    if job is None:
        raise error
    if isinstance(error, AudioTranscriptionError):
        code = error.code
        transient = error.transient
        user_message = error.user_message
    elif isinstance(error, AssemblyAIError):
        code = error.code
        transient = error.transient
        user_message = None
    else:
        code = type(error).__name__
        transient = True
        user_message = None
    job.attempts += 1
    if transient and job.attempts < job.max_attempts:
        job.status = "retrying"
        job.available_at = datetime.now(UTC) + timedelta(seconds=2**job.attempts)
        job.locked_by = None
        job.lease_expires_at = None
        job.error_code = code
        job.error_detail = "Falha temporária; uma nova tentativa foi programada."
        await session.commit()
        logger.warning(
            "audio_transcription_retrying",
            extra={"audio_job_id": str(job.id), "error_code": code},
        )
        return
    await _persist_terminal_failure(
        session,
        job,
        code=code,
        user_message=user_message
        or "Não consegui transcrever este áudio. Pode enviá-lo novamente ou escrever a mensagem?",
    )
    logger.warning(
        "audio_transcription_failed",
        extra={"audio_job_id": str(job.id), "error_code": code},
    )


async def process_audio_transcription_job(
    session: AsyncSession,
    job: AudioTranscriptionJob,
    *,
    settings: Settings,
    telegram: TelegramAudioClient,
    assemblyai: AssemblyAIClient,
) -> None:
    job_id = job.id
    try:
        inbound = await session.get(ChannelMessage, job.channel_message_id)
        if inbound is None or inbound.status == "failed":
            raise AudioTranscriptionError("audio_message_unavailable", transient=False)
        if job.duration_seconds > settings.assemblyai_max_audio_seconds:
            raise AudioTranscriptionError(
                "audio_too_long",
                transient=False,
                user_message="Para conversar por voz, envie áudios de até 2 minutos.",
            )
        if (
            job.file_size_bytes is not None
            and job.file_size_bytes > settings.assemblyai_max_audio_bytes
        ):
            raise AudioTranscriptionError(
                "audio_too_large",
                transient=False,
                user_message="Este áudio é grande demais. Envie uma mensagem de voz menor.",
            )

        if job.stage == "upload":
            destination = str(inbound.message_metadata.get("chat_id") or "")
            if destination:
                try:
                    await telegram.send_chat_action(destination)
                except Exception:
                    logger.info("telegram_chat_action_failed", extra={"audio_job_id": str(job.id)})
            audio = await telegram.download_file(
                job.telegram_file_id,
                settings.assemblyai_max_audio_bytes,
            )
            if not audio:
                raise AudioTranscriptionError("audio_empty", transient=False)
            job.file_size_bytes = len(audio)
            job.provider_upload_url = await assemblyai.upload_audio(audio)
            job.stage = "submit"
            _release(job)
            await session.commit()
            return

        if job.stage == "submit":
            if not job.provider_upload_url:
                raise AudioTranscriptionError("audio_upload_missing", transient=True)
            job.provider_transcript_id = await assemblyai.submit_transcript(
                job.provider_upload_url
            )
            job.provider_status = "queued"
            job.stage = "poll"
            _release(job, delay_seconds=settings.assemblyai_poll_interval_seconds)
            await session.commit()
            return

        if job.stage != "poll" or not job.provider_transcript_id:
            raise AudioTranscriptionError("audio_job_invalid_stage", transient=False)
        transcript = await assemblyai.get_transcript(job.provider_transcript_id)
        job.provider_status = transcript.status
        if transcript.status in {"queued", "processing"}:
            _release(job, delay_seconds=settings.assemblyai_poll_interval_seconds)
            await session.commit()
            return
        if transcript.status == "error":
            raise AudioTranscriptionError("assemblyai_transcription_error", transient=False)
        if transcript.status != "completed":
            raise AudioTranscriptionError("assemblyai_unknown_status", transient=True)
        text = (transcript.text or "").strip()
        if not text:
            raise AudioTranscriptionError(
                "audio_no_speech",
                transient=False,
                user_message="Não consegui entender este áudio. Pode falar novamente?",
            )

        now = datetime.now(UTC)
        confidence = transcript.confidence
        metadata = dict(inbound.message_metadata)
        metadata.update(
            {
                "input_type": "voice",
                "transcription_provider": "assemblyai",
                "transcription_model": transcript.speech_model_used or job.model,
                "transcription_confidence": confidence,
                "transcription_low_confidence": (
                    confidence is not None
                    and confidence < settings.assemblyai_min_confidence
                ),
                "transcription_language": transcript.language_code
                or settings.assemblyai_language_code,
            }
        )
        inbound.content = text
        inbound.message_metadata = metadata
        inbound.status = "received"
        inbound.available_at = now
        inbound.attempts = 0
        inbound.error_code = None
        job.status = "completed"
        job.language_code = transcript.language_code or settings.assemblyai_language_code
        job.confidence = confidence
        job.transcript_hash = hashlib.sha256(text.encode()).hexdigest()
        job.provider_latency_ms = max(
            0, int((now - _as_utc(job.created_at)).total_seconds() * 1000)
        )
        job.locked_by = None
        job.lease_expires_at = None
        job.error_code = None
        job.error_detail = None
        job.completed_at = now
        await session.commit()

        try:
            await assemblyai.delete_transcript(job.provider_transcript_id)
        except Exception:
            logger.info(
                "assemblyai_transcript_cleanup_failed",
                extra={"audio_job_id": str(job.id)},
            )
        else:
            fresh = await session.get(AudioTranscriptionJob, job.id)
            if fresh is not None:
                fresh.provider_upload_url = None
                fresh.provider_deleted_at = datetime.now(UTC)
                await session.commit()
    except Exception as exc:
        await _handle_failure(session, job_id, exc)
