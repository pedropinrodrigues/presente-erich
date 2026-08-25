from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import Identity, RequestContext
from agents_backend.config import Settings, get_settings
from agents_backend.errors import ConflictError, NotFoundError
from agents_backend.models import (
    AgentRun,
    AudioTranscriptionJob,
    ChannelAccount,
    ChannelMessage,
    Conversation,
    OrchestrationTask,
    OutboxMessage,
    PendingAction,
    ToolExecution,
)
from agents_backend.schemas import (
    AgentToolUseResponse,
    AgentTurnRequest,
    AgentTurnResponse,
    PendingActionResponse,
)

from .formatting import format_channel_text
from .phone_numbers import whatsapp_phone_aliases
from .providers import API_PROVIDER, TELEGRAM_PROVIDER, WHATSAPP_PROVIDER
from .router import route_command
from .runtime import ConversationAgent, ConversationAgentResult
from .telegram_commands import handle_account_command


def _pending_response(action: PendingAction | None) -> PendingActionResponse | None:
    if action is None:
        return None
    return PendingActionResponse(
        id=action.id,
        summary=action.summary,
        status=action.status,
        expires_at=action.expires_at,
    )


class ConversationService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        agent: ConversationAgent | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.agent = agent or ConversationAgent(settings=self.settings)

    async def _resolve_api_conversation(
        self,
        session: AsyncSession,
        context: RequestContext,
        conversation_id: uuid.UUID | None,
    ) -> Conversation:
        if conversation_id is not None:
            conversation = await session.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.workspace_id == context.workspace_id,
                    Conversation.user_id == context.identity.user_id,
                    Conversation.status == "active",
                )
            )
            if conversation is None:
                raise NotFoundError("Conversa não encontrada.")
            return conversation
        conversation = Conversation(
            workspace_id=context.workspace_id,
            user_id=context.identity.user_id,
            provider=API_PROVIDER,
            status="active",
            conversation_metadata={},
        )
        session.add(conversation)
        await session.flush()
        return conversation

    async def _replayed_response(
        self,
        session: AsyncSession,
        inbound: ChannelMessage,
        outbound: ChannelMessage,
    ) -> AgentTurnResponse:
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.inbound_message_id == inbound.id,
                AgentRun.run_type == "conversation",
            )
        )
        executions: list[ToolExecution] = []
        if run is not None:
            executions = list(
                (
                    await session.scalars(
                        select(ToolExecution)
                        .where(ToolExecution.agent_run_id == run.id)
                        .order_by(ToolExecution.created_at)
                    )
                ).all()
            )
        pending = await session.scalar(
            select(PendingAction)
            .where(
                PendingAction.created_by_message_id == inbound.id,
                PendingAction.status == "pending",
                PendingAction.expires_at > datetime.now(UTC),
            )
            .order_by(PendingAction.created_at.desc())
        )
        original_id = str(inbound.message_metadata.get("client_message_id") or "")
        orchestration_task = await session.scalar(
            select(OrchestrationTask).where(OrchestrationTask.inbound_message_id == inbound.id)
        )
        return AgentTurnResponse(
            conversation_id=inbound.conversation_id,
            message_id=original_id,
            answer=outbound.content,
            tools_used=[
                AgentToolUseResponse(
                    name=execution.tool_name,
                    status=execution.status,
                    risk_level=execution.risk_level,
                    idempotent_replay=True,
                )
                for execution in executions
            ],
            pending_action=_pending_response(pending),
            orchestration_task_id=(
                orchestration_task.id if orchestration_task is not None else None
            ),
            idempotent_replay=True,
        )

    async def process_api_turn(
        self,
        session: AsyncSession,
        context: RequestContext,
        request: AgentTurnRequest,
    ) -> AgentTurnResponse:
        external_message_id = f"{context.workspace_id}:{request.message_id}"
        existing = await session.scalar(
            select(ChannelMessage).where(
                ChannelMessage.provider == API_PROVIDER,
                ChannelMessage.external_message_id == external_message_id,
            )
        )
        if existing is not None:
            if existing.content != request.message:
                raise ConflictError(
                    "message_id_conflict",
                    "O message_id já foi usado com outro conteúdo.",
                )
            if (
                request.conversation_id is not None
                and existing.conversation_id != request.conversation_id
            ):
                raise ConflictError(
                    "message_conversation_conflict",
                    "O message_id pertence a outra conversa.",
                )
            outbound = await session.scalar(
                select(ChannelMessage)
                .where(ChannelMessage.reply_to_message_id == existing.id)
                .order_by(ChannelMessage.created_at)
                .limit(1)
            )
            if outbound is not None:
                return await self._replayed_response(session, existing, outbound)
            existing_run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.inbound_message_id == existing.id,
                    AgentRun.run_type == "conversation",
                )
            )
            if existing.status == "processing" and (
                existing_run is None or existing_run.status == "running"
            ):
                raise ConflictError(
                    "message_processing",
                    "Esta mensagem já está sendo processada.",
                )
            conversation = await session.get(Conversation, existing.conversation_id)
            if conversation is None:
                raise NotFoundError("Conversa não encontrada.")
            inbound = existing
            inbound.status = "processing"
            inbound.error_code = None
        else:
            conversation = await self._resolve_api_conversation(
                session, context, request.conversation_id
            )
            inbound = ChannelMessage(
                workspace_id=context.workspace_id,
                conversation_id=conversation.id,
                provider=API_PROVIDER,
                external_message_id=external_message_id,
                direction="inbound",
                content=request.message,
                status="processing",
                message_metadata={"client_message_id": request.message_id},
            )
            session.add(inbound)
        conversation.updated_at = datetime.now(UTC)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            concurrent = await session.scalar(
                select(ChannelMessage).where(
                    ChannelMessage.provider == API_PROVIDER,
                    ChannelMessage.external_message_id == external_message_id,
                )
            )
            if concurrent is None:
                raise
            if concurrent.content != request.message:
                raise ConflictError(
                    "message_id_conflict",
                    "O message_id já foi usado com outro conteúdo.",
                ) from None
            outbound = await session.scalar(
                select(ChannelMessage)
                .where(ChannelMessage.reply_to_message_id == concurrent.id)
                .order_by(ChannelMessage.created_at)
                .limit(1)
            )
            if outbound is not None:
                return await self._replayed_response(session, concurrent, outbound)
            raise ConflictError(
                "message_processing",
                "Esta mensagem já está sendo processada.",
            ) from None

        inbound_id = inbound.id
        account_command = await handle_account_command(
            session, context, inbound.content, self.settings
        )
        command = route_command(inbound.content) if account_command is None else None
        if account_command is not None:
            result = ConversationAgentResult(
                answer=account_command,
                run_id=uuid.UUID(int=0),
                tools_used=[],
                pending_action=None,
            )
        elif command is not None:
            result = ConversationAgentResult(
                answer=command.answer,
                run_id=uuid.UUID(int=0),
                tools_used=[],
                pending_action=None,
            )
        else:
            try:
                result = await self.agent.run(session, context, conversation, inbound)
            except Exception:
                await session.rollback()
                fresh_inbound = await session.get(ChannelMessage, inbound_id)
                if fresh_inbound is not None:
                    fresh_inbound.status = "failed"
                    fresh_inbound.error_code = "conversation_agent_unavailable"
                    await session.commit()
                raise

        outbound = ChannelMessage(
            workspace_id=context.workspace_id,
            conversation_id=conversation.id,
            reply_to_message_id=inbound.id,
            provider=API_PROVIDER,
            direction="outbound",
            content=result.answer,
            status="completed",
            message_metadata={
                "response_phase": (
                    "acknowledgement" if result.orchestration_task is not None else "final"
                )
            },
        )
        session.add(outbound)
        inbound.status = "completed"
        inbound.locked_by = None
        inbound.lease_expires_at = None
        await session.commit()
        return AgentTurnResponse(
            conversation_id=conversation.id,
            message_id=request.message_id,
            answer=result.answer,
            tools_used=result.tools_used,
            pending_action=_pending_response(result.pending_action),
            orchestration_task_id=(
                result.orchestration_task.id if result.orchestration_task is not None else None
            ),
            idempotent_replay=False,
        )

    async def ingest_whatsapp_text(
        self,
        session: AsyncSession,
        *,
        sender: str,
        phone_number_id: str,
        external_message_id: str,
        text: str,
        timestamp: str | None,
    ) -> tuple[ChannelMessage | None, bool]:
        account = await session.scalar(
            select(ChannelAccount).where(
                ChannelAccount.provider == WHATSAPP_PROVIDER,
                ChannelAccount.external_account_id.in_(whatsapp_phone_aliases(sender)),
                ChannelAccount.active.is_(True),
            )
        )
        if account is None:
            return None, False
        return await self._ingest_channel_text(
            session,
            account=account,
            provider=WHATSAPP_PROVIDER,
            external_thread_id=f"{phone_number_id}:{sender}",
            external_message_id=external_message_id,
            text=text,
            conversation_metadata={"phone_number_id": phone_number_id},
            message_metadata={
                "sender": sender,
                "phone_number_id": phone_number_id,
                "provider_timestamp": timestamp,
            },
        )

    async def ingest_telegram_text(
        self,
        session: AsyncSession,
        *,
        chat_id: str,
        user_id: str,
        external_message_id: str,
        text: str,
        timestamp: str | None,
    ) -> tuple[ChannelMessage | None, bool]:
        account = await session.scalar(
            select(ChannelAccount).where(
                ChannelAccount.provider == TELEGRAM_PROVIDER,
                ChannelAccount.external_account_id == chat_id,
                ChannelAccount.active.is_(True),
            )
        )
        if account is None:
            return None, False
        return await self._ingest_channel_text(
            session,
            account=account,
            provider=TELEGRAM_PROVIDER,
            external_thread_id=chat_id,
            external_message_id=external_message_id,
            text=text,
            conversation_metadata={"chat_id": chat_id},
            message_metadata={
                "sender": chat_id,
                "chat_id": chat_id,
                "telegram_user_id": user_id,
                "provider_timestamp": timestamp,
            },
        )

    async def ingest_telegram_voice(
        self,
        session: AsyncSession,
        *,
        chat_id: str,
        user_id: str,
        external_message_id: str,
        file_id: str,
        file_unique_id: str,
        duration_seconds: int,
        mime_type: str | None,
        file_size_bytes: int | None,
        timestamp: str | None,
    ) -> tuple[ChannelMessage | None, bool]:
        account = await session.scalar(
            select(ChannelAccount).where(
                ChannelAccount.provider == TELEGRAM_PROVIDER,
                ChannelAccount.external_account_id == chat_id,
                ChannelAccount.active.is_(True),
            )
        )
        if account is None:
            return None, False
        existing = await session.scalar(
            select(ChannelMessage).where(
                ChannelMessage.provider == TELEGRAM_PROVIDER,
                ChannelMessage.external_message_id == external_message_id,
            )
        )
        if existing is not None:
            return existing, True
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.provider == TELEGRAM_PROVIDER,
                Conversation.external_thread_id == chat_id,
            )
        )
        if conversation is None:
            conversation = Conversation(
                workspace_id=account.workspace_id,
                user_id=account.user_id,
                channel_account_id=account.id,
                provider=TELEGRAM_PROVIDER,
                external_thread_id=chat_id,
                status="active",
                conversation_metadata={"chat_id": chat_id},
            )
            session.add(conversation)
            await session.flush()
        inbound = ChannelMessage(
            workspace_id=account.workspace_id,
            conversation_id=conversation.id,
            provider=TELEGRAM_PROVIDER,
            external_message_id=external_message_id,
            direction="inbound",
            content="",
            status="transcribing",
            message_metadata={
                "input_type": "voice",
                "sender": chat_id,
                "chat_id": chat_id,
                "telegram_user_id": user_id,
                "provider_timestamp": timestamp,
                "telegram_file_unique_id": file_unique_id,
                "voice_duration_seconds": duration_seconds,
                "voice_mime_type": mime_type,
                "voice_file_size_bytes": file_size_bytes,
            },
        )
        session.add(inbound)
        await session.flush()
        session.add(
            AudioTranscriptionJob(
                workspace_id=account.workspace_id,
                conversation_id=conversation.id,
                channel_message_id=inbound.id,
                provider="assemblyai",
                model=self.settings.assemblyai_model,
                telegram_file_id=file_id,
                telegram_file_unique_id=file_unique_id,
                mime_type=mime_type,
                duration_seconds=duration_seconds,
                file_size_bytes=file_size_bytes,
                max_attempts=self.settings.worker_max_attempts,
            )
        )
        conversation.updated_at = datetime.now(UTC)
        await session.commit()
        return inbound, False

    async def _ingest_channel_text(
        self,
        session: AsyncSession,
        *,
        account: ChannelAccount,
        provider: str,
        external_thread_id: str,
        external_message_id: str,
        text: str,
        conversation_metadata: dict[str, str],
        message_metadata: dict[str, str | None],
    ) -> tuple[ChannelMessage, bool]:
        existing = await session.scalar(
            select(ChannelMessage).where(
                ChannelMessage.provider == provider,
                ChannelMessage.external_message_id == external_message_id,
            )
        )
        if existing is not None:
            return existing, True
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.provider == provider,
                Conversation.external_thread_id == external_thread_id,
            )
        )
        if conversation is None:
            conversation = Conversation(
                workspace_id=account.workspace_id,
                user_id=account.user_id,
                channel_account_id=account.id,
                provider=provider,
                external_thread_id=external_thread_id,
                status="active",
                conversation_metadata=conversation_metadata,
            )
            session.add(conversation)
            await session.flush()
        inbound = ChannelMessage(
            workspace_id=account.workspace_id,
            conversation_id=conversation.id,
            provider=provider,
            external_message_id=external_message_id,
            direction="inbound",
            content=text,
            status="received",
            message_metadata=message_metadata,
        )
        session.add(inbound)
        conversation.updated_at = datetime.now(UTC)
        await session.commit()
        return inbound, False

    async def process_channel_message(
        self, session: AsyncSession, inbound: ChannelMessage
    ) -> ConversationAgentResult:
        conversation = await session.get(Conversation, inbound.conversation_id)
        if conversation is None or conversation.status != "active":
            raise NotFoundError("Conversa não encontrada.")
        outbound = await session.scalar(
            select(ChannelMessage)
            .where(ChannelMessage.reply_to_message_id == inbound.id)
            .order_by(ChannelMessage.created_at)
            .limit(1)
        )
        if outbound is not None:
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.inbound_message_id == inbound.id,
                    AgentRun.run_type == "conversation",
                )
            )
            pending = await session.scalar(
                select(PendingAction)
                .where(
                    PendingAction.conversation_id == conversation.id,
                    PendingAction.status == "pending",
                    PendingAction.expires_at > datetime.now(UTC),
                )
                .order_by(PendingAction.created_at.desc())
            )
            return ConversationAgentResult(
                answer=outbound.content,
                run_id=run.id if run is not None else uuid.UUID(int=0),
                tools_used=[],
                pending_action=pending,
                orchestration_task=await session.scalar(
                    select(OrchestrationTask).where(
                        OrchestrationTask.inbound_message_id == inbound.id
                    )
                ),
            )
        context = RequestContext(
            identity=Identity(user_id=conversation.user_id),
            workspace_id=conversation.workspace_id,
        )
        account_command = await handle_account_command(
            session, context, inbound.content, self.settings
        )
        command = route_command(inbound.content) if account_command is None else None
        if account_command is not None:
            result = ConversationAgentResult(
                answer=account_command,
                run_id=uuid.UUID(int=0),
                tools_used=[],
                pending_action=None,
            )
        elif command is not None:
            result = ConversationAgentResult(
                answer=command.answer,
                run_id=uuid.UUID(int=0),
                tools_used=[],
                pending_action=None,
            )
        else:
            result = await self.agent.run(session, context, conversation, inbound)
        outbound_text = format_channel_text(conversation.provider, result.answer)
        outbound = ChannelMessage(
            workspace_id=conversation.workspace_id,
            conversation_id=conversation.id,
            reply_to_message_id=inbound.id,
            provider=conversation.provider,
            direction="outbound",
            content=outbound_text,
            status="queued",
            message_metadata={
                "response_phase": (
                    "acknowledgement" if result.orchestration_task is not None else "final"
                )
            },
        )
        session.add(outbound)
        await session.flush()
        sender = str(inbound.message_metadata.get("sender") or "")
        destination = sender
        if conversation.channel_account_id is not None:
            account = await session.get(ChannelAccount, conversation.channel_account_id)
            if account is not None and account.provider == conversation.provider:
                destination = account.external_account_id
        outbox = OutboxMessage(
            workspace_id=conversation.workspace_id,
            conversation_id=conversation.id,
            channel_message_id=outbound.id,
            provider=conversation.provider,
            destination=destination,
            payload={"type": "text", "text": {"body": outbound_text}},
            status="pending",
            idempotency_key=f"reply:{inbound.id}",
        )
        session.add(outbox)
        await session.flush()
        if result.orchestration_task is not None:
            result.orchestration_task.ack_outbox_id = outbox.id
        inbound.status = "completed"
        inbound.locked_by = None
        inbound.lease_expires_at = None
        await session.commit()
        return result
