from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.config import Settings
from agents_backend.conversation.tools import (
    TOOL_VERSION,
    ToolContext,
    ToolEnvelope,
    ToolSpec,
    _failure,
    _success,
    create_pending_action,
)
from agents_backend.integrations.bitrix24.crypto import (
    decrypt_json,
    decrypt_secret,
    encrypt_json,
    encrypt_secret,
    fingerprint,
)
from agents_backend.integrations.bitrix24.gateway import (
    Bitrix24AuthenticationError,
    Bitrix24Gateway,
    Bitrix24ToolError,
)
from agents_backend.integrations.bitrix24.policies import (
    POLICIES,
    BitrixToolPolicy,
    EmptyArguments,
)
from agents_backend.integrations.bitrix24.results import normalize_result
from agents_backend.models import (
    ExternalAction,
    ExternalConnectionRequest,
    ExternalIntegration,
    PendingAction,
)

PROVIDER = "bitrix24"
TOOLKIT = "bitrix24_mcp"


def _connection_data(integration: ExternalIntegration) -> dict[str, Any]:
    return {
        "connection_id": str(integration.id),
        "status": integration.status,
        "connected": integration.status == "active",
        "label": integration.account_label or "Bitrix24",
        "last_verified_at": (
            integration.last_verified_at.isoformat() if integration.last_verified_at else None
        ),
    }


async def _integration(
    context: ToolContext, *, active_only: bool = False
) -> ExternalIntegration | None:
    statement = select(ExternalIntegration).where(
        ExternalIntegration.workspace_id == context.request_context.workspace_id,
        ExternalIntegration.user_id == context.request_context.identity.user_id,
        ExternalIntegration.provider == PROVIDER,
        ExternalIntegration.toolkit_slug == TOOLKIT,
    )
    if active_only:
        statement = statement.where(ExternalIntegration.status == "active")
    return await context.session.scalar(statement.order_by(ExternalIntegration.updated_at.desc()))


async def connect_bitrix24(context: ToolContext, _: EmptyArguments) -> ToolEnvelope:
    current = await _integration(context)
    if current is not None and current.status == "active":
        return _success(
            "bitrix24_already_connected", "O Bitrix24 já está conectado.", _connection_data(current)
        )
    if current is not None:
        current.credential_ciphertext = None
        current.credential_expires_at = None
        current.status = "revoked"
        current.revoked_at = datetime.now(UTC)
        await context.session.execute(
            update(ExternalConnectionRequest)
            .where(
                ExternalConnectionRequest.integration_id == current.id,
                ExternalConnectionRequest.status.in_(["pending", "awaiting_confirmation"]),
            )
            .values(status="cancelled", completed_at=datetime.now(UTC))
        )
    state = secrets.token_urlsafe(32)
    request_id = uuid.uuid4()
    integration = ExternalIntegration(
        workspace_id=context.request_context.workspace_id,
        user_id=context.request_context.identity.user_id,
        provider=PROVIDER,
        toolkit_slug=TOOLKIT,
        auth_config_id="connection_token",
        connected_account_id=str(request_id),
        credential_kind="connection_token",
        status="pending_token",
        account_label="Bitrix24",
        is_default=True,
        integration_metadata={"mcp_endpoint": context.settings.bitrix24_mcp_url},
    )
    context.session.add(integration)
    await context.session.flush()
    context.session.add(
        ExternalConnectionRequest(
            id=request_id,
            workspace_id=context.request_context.workspace_id,
            user_id=context.request_context.identity.user_id,
            conversation_id=context.conversation.id,
            integration_id=integration.id,
            provider=PROVIDER,
            toolkit_slug=TOOLKIT,
            auth_config_id="connection_token",
            status="pending",
            composio_request_id=str(request_id),
            callback_state_hash=hashlib.sha256(state.encode()).hexdigest(),
            created_by_message_id=context.inbound_message.id,
            orchestration_task_id=(
                context.orchestration_task.id if context.orchestration_task else None
            ),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=context.settings.bitrix24_connection_ttl_seconds),
        )
    )
    # The opaque state stays in the URL fragment, which browsers do not send to access logs.
    base_url = str(context.settings.bitrix24_public_base_url).rstrip("/")
    url = f"{base_url}/connect/bitrix24#state={state}"
    return _success(
        "bitrix24_connection_token_required",
        "Abra o link privado, insira o token de conexão do Bitrix24 e depois confirme no canal.",
        {
            "status": "pending_token",
            "authorization_url": url,
            "expires_in_seconds": context.settings.bitrix24_connection_ttl_seconds,
            "connection_id": str(integration.id),
        },
    )


async def get_bitrix24_connection_status(context: ToolContext, _: EmptyArguments) -> ToolEnvelope:
    integration = await _integration(context)
    if integration is None:
        return _success(
            "bitrix24_not_connected",
            "O Bitrix24 ainda não está conectado.",
            {"status": "not_connected", "connected": False},
        )
    if integration.status == "active" and integration.credential_ciphertext:
        try:
            await Bitrix24Gateway(context.settings).list_tools(
                decrypt_secret(context.settings, integration.credential_ciphertext)
            )
            integration.last_verified_at = datetime.now(UTC)
        except Bitrix24AuthenticationError:
            integration.status = "authorization_failed"
            integration.credential_ciphertext = None
        except Exception:
            return _failure(
                "bitrix24_status_unavailable",
                "Não foi possível verificar o Bitrix24 agora.",
                retryable=True,
            )
    return _success(
        "bitrix24_status_checked",
        "Estado da conexão Bitrix24 consultado.",
        _connection_data(integration),
    )


async def disconnect_bitrix24(context: ToolContext, _: EmptyArguments) -> ToolEnvelope:
    integration = await _integration(context)
    if integration is None or integration.status == "revoked":
        return _success("bitrix24_not_connected", "Não há conexão Bitrix24 ativa.")
    integration.credential_ciphertext = None
    integration.credential_expires_at = None
    integration.status = "revoked"
    integration.revoked_at = datetime.now(UTC)
    return _success(
        "bitrix24_disconnected",
        "A credencial local foi removida. Revogue também o token no Bitrix24.",
        _connection_data(integration),
    )


async def submit_connection_token(
    session: AsyncSession, settings: Settings, state: str, token: str
) -> tuple[bool, str]:
    now = datetime.now(UTC)
    request = await session.scalar(
        select(ExternalConnectionRequest)
        .where(
            ExternalConnectionRequest.provider == PROVIDER,
            ExternalConnectionRequest.callback_state_hash
            == hashlib.sha256(state.encode()).hexdigest(),
        )
        .with_for_update()
    )
    expires_at = request.expires_at if request is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if request is None or request.status != "pending" or expires_at is None or expires_at <= now:
        return False, "Este link é inválido, expirou ou já foi utilizado."
    request.attempts += 1
    if request.attempts > settings.bitrix24_connection_max_attempts:
        request.status = "failed"
        return False, "O limite de tentativas deste link foi atingido."
    integration = await session.get(ExternalIntegration, request.integration_id)
    if integration is None:
        request.status = "failed"
        return False, "A solicitação de conexão não existe mais."
    try:
        tools = await Bitrix24Gateway(settings).list_tools(token)
    except Exception:
        if request.attempts >= settings.bitrix24_connection_max_attempts:
            request.status = "failed"
            integration.status = "verification_failed"
        return False, "O token não pôde ser validado no Bitrix24. Confira e tente novamente."
    names = sorted(str(tool.get("name")) for tool in tools if tool.get("name"))
    confirmation_expires_at = now + timedelta(seconds=settings.pending_action_ttl_seconds)
    integration.credential_ciphertext = encrypt_secret(settings, token)
    integration.credential_fingerprint = fingerprint(token)
    integration.credential_expires_at = confirmation_expires_at
    integration.status = "awaiting_confirmation"
    integration.last_verified_at = now
    integration.integration_metadata = {
        **integration.integration_metadata,
        "available_tools": names[:200],
        "available_tool_count": len(names),
    }
    request.status = "awaiting_confirmation"
    request.expires_at = confirmation_expires_at
    request.submitted_at = now
    if request.conversation_id is None or request.created_by_message_id is None:
        request.status = "failed"
        integration.credential_ciphertext = None
        integration.status = "failed"
        return False, "Não foi possível vincular a confirmação ao canal original."
    arguments = {"connection_request_id": str(request.id)}
    signature = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
    session.add(
        PendingAction(
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            created_by_message_id=request.created_by_message_id,
            orchestration_task_id=request.orchestration_task_id,
            tool_name="activate_bitrix_connection",
            tool_version=TOOL_VERSION,
            arguments=arguments,
            summary="Ativar a conexão com o Bitrix24 usando o token validado",
            confirmation_token=f"{signature[:16]}:{secrets.token_urlsafe(24)}",
            status="pending",
            expires_at=confirmation_expires_at,
        )
    )
    return True, "Token validado. Volte ao canal da conversa e envie “confirmo” para ativar."


async def activate_pending_connection(context: ToolContext, pending: PendingAction) -> ToolEnvelope:
    request = await context.session.get(
        ExternalConnectionRequest, uuid.UUID(str(pending.arguments["connection_request_id"]))
    )
    if (
        request is None
        or request.workspace_id != context.request_context.workspace_id
        or request.user_id != context.request_context.identity.user_id
        or request.status != "awaiting_confirmation"
    ):
        return _failure(
            "bitrix24_connection_not_ready", "A conexão Bitrix24 não está pronta para ativação."
        )
    integration = await context.session.get(ExternalIntegration, request.integration_id)
    if integration is None or not integration.credential_ciphertext:
        return _failure(
            "bitrix24_connection_not_ready", "A credencial Bitrix24 não está disponível."
        )
    integration.status = "active"
    integration.credential_expires_at = None
    integration.last_verified_at = datetime.now(UTC)
    request.status = "completed"
    request.completed_at = datetime.now(UTC)
    return _success(
        "bitrix24_connected", "O Bitrix24 foi conectado com sucesso.", _connection_data(integration)
    )


async def cancel_pending_connection(context: ToolContext, pending: PendingAction) -> None:
    raw_id = pending.arguments.get("connection_request_id")
    if not raw_id:
        return
    request = await context.session.get(ExternalConnectionRequest, uuid.UUID(str(raw_id)))
    if request is None or request.workspace_id != context.request_context.workspace_id:
        return
    request.status = "cancelled"
    request.completed_at = datetime.now(UTC)
    integration = await context.session.get(ExternalIntegration, request.integration_id)
    if integration is not None:
        integration.credential_ciphertext = None
        integration.credential_expires_at = None
        integration.status = "revoked"
        integration.revoked_at = datetime.now(UTC)


def _redact(arguments: dict[str, Any]) -> dict[str, Any]:
    result = dict(arguments)
    for key in {"description", "title", "query"} & result.keys():
        value = json.dumps(result[key], ensure_ascii=False)
        result[key] = {
            "redacted": True,
            "length": len(value),
            "sha256": hashlib.sha256(value.encode()).hexdigest(),
        }
    return result


async def _run_action(context: ToolContext, action: ExternalAction) -> ToolEnvelope:
    integration = await context.session.get(ExternalIntegration, action.integration_id)
    if (
        integration is None
        or integration.status != "active"
        or not integration.credential_ciphertext
    ):
        action.status = "failed"
        action.error_code = "bitrix24_connection_required"
        return _failure(
            "bitrix24_connection_required", "O Bitrix24 precisa ser conectado novamente."
        )
    action.status = "executing"
    action.executed_at = datetime.now(UTC)
    try:
        payload = await Bitrix24Gateway(context.settings).execute(
            decrypt_secret(context.settings, integration.credential_ciphertext),
            action.tool_slug,
            decrypt_json(context.settings, action.arguments_ciphertext),
        )
    except Bitrix24AuthenticationError:
        integration.status = "authorization_failed"
        integration.credential_ciphertext = None
        action.status = "failed"
        action.error_code = "bitrix24_authorization_failed"
        action.completed_at = datetime.now(UTC)
        return _failure("bitrix24_authorization_failed", "O token Bitrix24 precisa ser renovado.")
    except Bitrix24ToolError as exc:
        action.status = "failed"
        action.error_code = "bitrix24_tool_rejected"
        action.completed_at = datetime.now(UTC)
        return _failure("bitrix24_tool_rejected", str(exc))
    except Exception:
        uncertain_write = action.risk_level == "R2"
        action.status = "outcome_unknown" if uncertain_write else "failed"
        action.error_code = (
            "bitrix24_outcome_unknown" if uncertain_write else "bitrix24_execution_failed"
        )
        action.completed_at = datetime.now(UTC)
        return _failure(
            action.error_code,
            (
                "A resposta da alteração no Bitrix24 foi perdida. Não vou repetir a operação; "
                "confira o registro antes de tentar novamente."
                if uncertain_write
                else "Não foi possível concluir a consulta no Bitrix24."
            ),
            retryable=not uncertain_write,
        )
    result = normalize_result(payload)
    integration.last_verified_at = datetime.now(UTC)
    action.status = "completed"
    action.result_sanitized = result
    action.completed_at = datetime.now(UTC)
    return _success("bitrix24_action_completed", "A operação no Bitrix24 foi concluída.", result)


async def execute_policy(
    context: ToolContext, policy: BitrixToolPolicy, values: dict[str, Any]
) -> ToolEnvelope:
    integration = await _integration(context, active_only=True)
    if integration is None:
        return _failure("bitrix24_connection_required", "Conecte o Bitrix24 antes desta operação.")
    remote_tool = policy.remote_tool(context.settings)
    if not remote_tool:
        return _failure(
            "bitrix24_tool_not_configured",
            "Esta operação Bitrix24 ainda não foi mapeada no ambiente.",
        )
    arguments = {key: value for key, value in values.items() if value is not None}
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    arguments_hash = hashlib.sha256(encoded.encode()).hexdigest()
    idempotency_key = hashlib.sha256(
        f"{context.idempotency_key}:{integration.id}:{remote_tool}:{arguments_hash}".encode()
    ).hexdigest()
    existing = await context.session.scalar(
        select(ExternalAction).where(
            ExternalAction.workspace_id == context.request_context.workspace_id,
            ExternalAction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.status == "completed" and existing.result_sanitized is not None:
            return _success(
                "bitrix24_action_completed",
                "A operação já havia sido concluída.",
                existing.result_sanitized,
            )
        if existing.status == "outcome_unknown":
            return _failure(
                "bitrix24_outcome_unknown",
                "O resultado desta alteração é incerto. Confira o registro no Bitrix24; "
                "a operação não será repetida automaticamente.",
            )
        if existing.status == "failed":
            return _failure(
                existing.error_code or "bitrix24_action_failed",
                "Esta operação Bitrix24 já falhou e não será repetida automaticamente.",
            )
        if policy.risk == "R0" and existing.status in {"ready", "executing"}:
            return await _run_action(context, existing)
        if existing.pending_action_id:
            pending = await context.session.get(PendingAction, existing.pending_action_id)
            if pending and pending.status == "pending":
                return _success(
                    "confirmation_required",
                    "A operação ainda aguarda confirmação.",
                    {
                        "action_id": str(pending.id),
                        "summary": pending.summary,
                        "expires_at": pending.expires_at.isoformat(),
                    },
                )
        return _failure("bitrix24_action_in_progress", "A operação Bitrix24 já está em andamento.")
    action = ExternalAction(
        workspace_id=context.request_context.workspace_id,
        user_id=context.request_context.identity.user_id,
        conversation_id=context.conversation.id,
        orchestration_task_id=context.orchestration_task.id if context.orchestration_task else None,
        integration_id=integration.id,
        provider=PROVIDER,
        toolkit_slug=TOOLKIT,
        tool_slug=remote_tool,
        risk_level=policy.risk,
        arguments_sanitized=_redact(arguments),
        arguments_ciphertext=encrypt_json(context.settings, arguments),
        arguments_hash=arguments_hash,
        idempotency_key=idempotency_key,
        status="proposed" if policy.risk == "R2" else "ready",
    )
    context.session.add(action)
    await context.session.flush()
    if policy.risk == "R0":
        return await _run_action(context, action)
    pending = await create_pending_action(
        context,
        tool_name="bitrix_external_action",
        arguments={"external_action_id": str(action.id)},
        summary=f"Confirmar no Bitrix24: {policy.description}",
    )
    action.pending_action_id = pending.id
    return _success(
        "confirmation_required",
        "A alteração não foi executada. Confirme em uma nova mensagem.",
        {
            "action_id": str(pending.id),
            "summary": pending.summary,
            "expires_at": pending.expires_at.isoformat(),
        },
    )


async def execute_pending_action(context: ToolContext, pending: PendingAction) -> ToolEnvelope:
    action = await context.session.get(
        ExternalAction, uuid.UUID(str(pending.arguments["external_action_id"]))
    )
    if (
        action is None
        or action.workspace_id != context.request_context.workspace_id
        or action.provider != PROVIDER
    ):
        return _failure(
            "bitrix24_action_not_found", "A operação Bitrix24 pendente não foi encontrada."
        )
    action.confirmed_at = datetime.now(UTC)
    return await _run_action(context, action)


async def expire_stale_connections(session: AsyncSession) -> bool:
    """Delete credentials that were validated but never confirmed in the channel."""
    now = datetime.now(UTC)
    integrations = await session.execute(
        update(ExternalIntegration)
        .where(
            ExternalIntegration.provider == PROVIDER,
            ExternalIntegration.status == "awaiting_confirmation",
            ExternalIntegration.credential_expires_at <= now,
        )
        .values(
            credential_ciphertext=None,
            credential_expires_at=None,
            status="expired",
            revoked_at=now,
        )
    )
    requests = await session.execute(
        update(ExternalConnectionRequest)
        .where(
            ExternalConnectionRequest.provider == PROVIDER,
            ExternalConnectionRequest.status == "awaiting_confirmation",
            ExternalConnectionRequest.expires_at <= now,
        )
        .values(status="expired", completed_at=now)
    )
    pending = await session.execute(
        update(PendingAction)
        .where(
            PendingAction.tool_name == "activate_bitrix_connection",
            PendingAction.status == "pending",
            PendingAction.expires_at <= now,
        )
        .values(status="expired")
    )
    changed = any(int(result.rowcount or 0) > 0 for result in (integrations, requests, pending))
    if changed:
        await session.commit()
    else:
        await session.rollback()
    return changed


def bitrix24_tool_specs(capabilities: list[str], settings: Settings) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    if "bitrix_connection" in capabilities:
        specs.extend(
            [
                ToolSpec(
                    "connect_bitrix24",
                    "Gera um link privado e temporário para conectar o token MCP do Bitrix24.",
                    EmptyArguments,
                    "R1",
                    connect_bitrix24,
                ),
                ToolSpec(
                    "get_bitrix24_connection_status",
                    "Verifica a conexão MCP do Bitrix24 sem ler dados de CRM ou tarefas.",
                    EmptyArguments,
                    "R0",
                    get_bitrix24_connection_status,
                ),
                ToolSpec(
                    "disconnect_bitrix24",
                    "Remove localmente a credencial Bitrix24. Oriente o usuário a revogar "
                    "também no portal.",
                    EmptyArguments,
                    "R1",
                    disconnect_bitrix24,
                ),
            ]
        )
    for policy in POLICIES:
        if policy.capability not in capabilities or not policy.remote_tool(settings):
            continue

        async def handler(
            context: ToolContext, arguments: Any, selected: BitrixToolPolicy = policy
        ) -> ToolEnvelope:
            return await execute_policy(context, selected, arguments.model_dump(mode="json"))

        specs.append(
            ToolSpec(policy.name, policy.description, policy.arguments_model, policy.risk, handler)
        )
    return specs
