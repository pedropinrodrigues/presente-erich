from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import Identity, RequestContext
from agents_backend.config import Settings
from agents_backend.ingestion.service import ingest_transcript
from agents_backend.models import ChannelMessage, Conversation, OrchestrationTask, Source
from agents_backend.schemas import ExtractionResult, TranscriptEvent

SOURCE_TYPE = "daily_conversation"
POLICY_VERSION = "user-evidence-v2"
CAPTURE_NAMESPACE = uuid.UUID("49a521de-e320-42b3-a5ee-2ebff162450c")


def daily_capture_id(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    local_date: date,
    chunk_index: int,
) -> uuid.UUID:
    key = (
        f"{POLICY_VERSION}:{workspace_id}:{user_id}:"
        f"{local_date.isoformat()}:{chunk_index}"
    )
    return uuid.uuid5(CAPTURE_NAMESPACE, key)


def _bounds(local_date: date, timezone: tzinfo) -> tuple[datetime, datetime]:
    start = datetime.combine(local_date, time.min, tzinfo=timezone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _is_human_inbound(message: ChannelMessage) -> bool:
    if message.direction != "inbound" or message.status != "completed":
        return False
    if not message.content.strip():
        return False
    if message.message_metadata.get("origin") == "scheduled_automation":
        return False
    external_id = str(message.external_message_id or "")
    return not external_id.startswith(("schedule:", "deploy-canary:"))


def _lines(
    message: ChannelMessage,
    conversation: Conversation,
    timezone: tzinfo,
    maximum: int,
    *,
    memory_eligible: bool,
) -> list[str]:
    timestamp = message.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    base_payload = {
        "at": timestamp.astimezone(timezone).isoformat(),
        "conversation_id": str(conversation.id),
        "channel": conversation.provider,
        "role": "user" if message.direction == "inbound" else "assistant",
        "memory_eligible": memory_eligible,
    }
    overhead = len(
        json.dumps(
            {**base_payload, "content": "", "part": 1, "parts": 1},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    # JSON control characters may expand to six characters (for example, ``\u0000``).
    content_size = max(1, (maximum - overhead - 10) // 6)
    parts = [
        message.content[index : index + content_size]
        for index in range(0, len(message.content), content_size)
    ] or [""]
    return [
        json.dumps(
            {
                **base_payload,
                "content": content,
                **({"part": index + 1, "parts": len(parts)} if len(parts) > 1 else {}),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for index, content in enumerate(parts)
    ]


def _chunks(lines: list[str], maximum: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        line_size = len(line) + 1
        if current and current_size + line_size > maximum:
            chunks.append("\n".join(current))
            current = []
            current_size = 0
        if line_size > maximum:
            raise ValueError("A linha diária excede o limite configurado")
        current.append(line)
        current_size += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _messages_for_day(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> dict[
    tuple[uuid.UUID, uuid.UUID],
    tuple[list[ChannelMessage], dict[uuid.UUID, Conversation], set[uuid.UUID]],
]:
    inbound_rows = list(
        (
            await session.execute(
                select(ChannelMessage, Conversation)
                .join(Conversation, Conversation.id == ChannelMessage.conversation_id)
                .where(
                    ChannelMessage.created_at >= start,
                    ChannelMessage.created_at < end,
                    ChannelMessage.direction == "inbound",
                    ChannelMessage.status == "completed",
                )
                .order_by(ChannelMessage.created_at, ChannelMessage.id)
            )
        ).all()
    )
    human_rows = [
        (message, conversation)
        for message, conversation in inbound_rows
        if _is_human_inbound(message)
    ]
    if not human_rows:
        return {}
    inbound_ids = [message.id for message, _ in human_rows]
    delegated_ids = set(
        (
            await session.scalars(
                select(OrchestrationTask.inbound_message_id).where(
                    OrchestrationTask.inbound_message_id.in_(inbound_ids)
                )
            )
        ).all()
    )
    eligible_ids = {
        message.id
        for message, _ in human_rows
        if message.id not in delegated_ids and not message.content.lstrip().startswith("/")
    }
    outbound_rows = list(
        (
            await session.execute(
                select(ChannelMessage, Conversation)
                .join(Conversation, Conversation.id == ChannelMessage.conversation_id)
                .where(
                    ChannelMessage.reply_to_message_id.in_(inbound_ids),
                    ChannelMessage.direction == "outbound",
                    ChannelMessage.status == "completed",
                )
                .order_by(ChannelMessage.created_at, ChannelMessage.id)
            )
        ).all()
    )
    grouped_messages: dict[tuple[uuid.UUID, uuid.UUID], list[ChannelMessage]] = defaultdict(list)
    grouped_conversations: dict[
        tuple[uuid.UUID, uuid.UUID], dict[uuid.UUID, Conversation]
    ] = defaultdict(dict)
    for message, conversation in [*human_rows, *outbound_rows]:
        key = (conversation.workspace_id, conversation.user_id)
        grouped_messages[key].append(message)
        grouped_conversations[key][conversation.id] = conversation
    return {
        key: (
            sorted(messages, key=lambda item: (item.created_at, item.id)),
            grouped_conversations[key],
            eligible_ids,
        )
        for key, messages in grouped_messages.items()
    }


async def dispatch_daily_conversation_memory(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> bool:
    if not settings.daily_conversation_memory_enabled:
        await session.rollback()
        return False
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    timezone = ZoneInfo(settings.app_timezone)
    local_now = current.astimezone(timezone)
    if local_now.hour < settings.daily_conversation_memory_hour:
        await session.rollback()
        return False

    for offset in range(settings.daily_conversation_memory_lookback_days, 0, -1):
        local_date = local_now.date() - timedelta(days=offset)
        start, end = _bounds(local_date, timezone)
        groups = await _messages_for_day(session, start, end)
        for (workspace_id, user_id), (messages, conversations, eligible_ids) in groups.items():
            lines = [
                line
                for message in messages
                for line in _lines(
                    message,
                    conversations[message.conversation_id],
                    timezone,
                    settings.daily_conversation_memory_chunk_characters,
                    memory_eligible=(message.direction == "inbound" and message.id in eligible_ids),
                )
            ]
            chunks = _chunks(lines, settings.daily_conversation_memory_chunk_characters)
            capture_ids = [
                daily_capture_id(workspace_id, user_id, local_date, index)
                for index in range(len(chunks))
            ]
            existing = set(
                (
                    await session.scalars(
                        select(Source.capture_id).where(
                            Source.workspace_id == workspace_id,
                            Source.capture_id.in_(capture_ids),
                        )
                    )
                ).all()
            )
            if len(existing) == len(capture_ids):
                continue
            context = RequestContext(
                identity=Identity(user_id=user_id),
                workspace_id=workspace_id,
            )
            try:
                for index, transcript in enumerate(chunks):
                    if capture_ids[index] in existing:
                        continue
                    await ingest_transcript(
                        session,
                        context,
                        TranscriptEvent(
                            capture_id=capture_ids[index],
                            source=SOURCE_TYPE,
                            captured_at=end - timedelta(microseconds=1),
                            transcript=transcript,
                            language="pt-BR",
                            metadata={
                                "local_date": local_date.isoformat(),
                                "timezone": settings.app_timezone,
                                "chunk_index": index,
                                "chunk_count": len(chunks),
                                "message_count": len(messages),
                                "conversation_ids": sorted(str(value) for value in conversations),
                                "memory_policy": "user_authored_only",
                                "memory_policy_version": POLICY_VERSION,
                            },
                        ),
                        commit=False,
                    )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return True
    await session.rollback()
    return False


def filter_daily_conversation_extraction(
    transcript: str,
    result: ExtractionResult,
) -> ExtractionResult:
    eligible_lines: set[str] = set()
    for raw_line in transcript.splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("role") == "user"
            and payload.get("memory_eligible") is True
        ):
            eligible_lines.add(raw_line.strip())

    def supported(candidate: object) -> bool:
        evidence = getattr(candidate, "evidence", None)
        excerpt = str(getattr(evidence, "excerpt", "")).strip()
        return excerpt in eligible_lines

    entities = [candidate for candidate in result.entities if supported(candidate)]
    entity_ids = {candidate.candidate_id for candidate in entities}
    facts = [
        candidate
        for candidate in result.facts
        if supported(candidate)
        and (
            candidate.subject_candidate_id is None
            or candidate.subject_candidate_id in entity_ids
        )
    ]
    commitments = [
        candidate
        for candidate in result.commitments
        if supported(candidate)
        and (
            candidate.responsible_candidate_id is None
            or candidate.responsible_candidate_id in entity_ids
        )
    ]
    return result.model_copy(
        update={
            "entities": entities,
            "facts": facts,
            "commitments": commitments,
        }
    )
