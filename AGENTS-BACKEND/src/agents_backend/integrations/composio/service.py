from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet
from sqlalchemy import select, update

from agents_backend.conversation.tools import (
    ToolContext,
    ToolEnvelope,
    ToolSpec,
    _failure,
    _success,
)
from agents_backend.integrations.composio.gateway import (
    ComposioGateway,
    ComposioToolExecutionError,
)
from agents_backend.integrations.composio.policies import (
    POLICIES,
    ConfigureExternalAccountArguments,
    ConnectExternalAppArguments,
    ExternalToolPolicy,
    GetExternalConnectionStatusArguments,
    ListExternalAccountsArguments,
    remote_arguments,
)
from agents_backend.integrations.composio.results import (
    bound_normalized_result,
    normalize_mcp_result,
)
from agents_backend.models import (
    ExternalAction,
    ExternalConnectionRequest,
    ExternalIntegration,
    PendingAction,
)

TOOLKIT_LABELS = {
    "gmail": "Gmail",
    "googlecalendar": "Google Calendar",
    "whatsapp": "WhatsApp Business",
}


def _secret(settings: Any) -> bytes:
    return settings.composio_user_id_secret.get_secret_value().encode()


def opaque_user_id(context: ToolContext) -> str:
    raw = f"{context.request_context.workspace_id}:{context.request_context.identity.user_id}"
    return "usr_" + hmac.new(_secret(context.settings), raw.encode(), hashlib.sha256).hexdigest()


def auth_config_id(settings: Any, toolkit: str) -> str:
    value = {
        "gmail": settings.composio_gmail_auth_config_id,
        "googlecalendar": settings.composio_googlecalendar_auth_config_id,
        "whatsapp": settings.composio_whatsapp_auth_config_id,
    }[toolkit]
    return str(value)


def _callback_url(base: str, state: str) -> str:
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["state"] = state
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _fernet(context: ToolContext) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret(context.settings)).digest())
    return Fernet(key)


def _encrypt(context: ToolContext, arguments: dict[str, Any]) -> str:
    return _fernet(context).encrypt(json.dumps(arguments, ensure_ascii=False).encode()).decode()


def _decrypt(context: ToolContext, ciphertext: str) -> dict[str, Any]:
    value = json.loads(_fernet(context).decrypt(ciphertext.encode()))
    if not isinstance(value, dict):
        raise ValueError("argumentos externos inválidos")
    return value


def _redact(arguments: dict[str, Any]) -> dict[str, Any]:
    sensitive = {"body", "text", "description", "recipient_email", "to_number", "attendees"}
    result: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in sensitive:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            result[key] = {
                "redacted": True,
                "length": len(encoded),
                "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            }
        else:
            result[key] = value
    return result


def _account_data(integration: ExternalIntegration) -> dict[str, Any]:
    return {
        "account_id": str(integration.id),
        "label": integration.account_label or integration.display_name or "Conta sem nome",
        "display_name": integration.display_name,
        "toolkit": integration.toolkit_slug,
        "status": integration.status,
        "is_default": integration.is_default,
    }


async def _integrations(
    context: ToolContext,
    toolkit: str | None = None,
    *,
    active_only: bool = False,
) -> list[ExternalIntegration]:
    statement = select(ExternalIntegration).where(
        ExternalIntegration.workspace_id == context.request_context.workspace_id,
        ExternalIntegration.user_id == context.request_context.identity.user_id,
    )
    if toolkit is not None:
        statement = statement.where(ExternalIntegration.toolkit_slug == toolkit)
    if active_only:
        statement = statement.where(ExternalIntegration.status == "active")
    return list(
        (
            await context.session.scalars(
                statement.order_by(
                    ExternalIntegration.is_default.desc(),
                    ExternalIntegration.updated_at.desc(),
                )
            )
        ).all()
    )


async def _active_integration(context: ToolContext, toolkit: str) -> ExternalIntegration | None:
    integrations = await _integrations(context, toolkit, active_only=True)
    return integrations[0] if integrations else None


async def _scoped_integration(
    context: ToolContext,
    toolkit: str,
    account_id: uuid.UUID,
    *,
    active_only: bool = True,
) -> ExternalIntegration | None:
    statement = select(ExternalIntegration).where(
        ExternalIntegration.workspace_id == context.request_context.workspace_id,
        ExternalIntegration.user_id == context.request_context.identity.user_id,
        ExternalIntegration.toolkit_slug == toolkit,
        ExternalIntegration.id == account_id,
    )
    if active_only:
        statement = statement.where(ExternalIntegration.status == "active")
    return await context.session.scalar(statement)


async def _verified_remote_status(context: ToolContext, integration: ExternalIntegration) -> str:
    account = await ComposioGateway(context.settings).get_connected_account(
        integration.connected_account_id
    )
    remote_user_id = str(getattr(account, "user_id", ""))
    if remote_user_id and remote_user_id != opaque_user_id(context):
        raise RuntimeError("A conta externa não pertence ao usuário atual")
    return str(getattr(account, "status", "UNKNOWN")).upper()


async def get_external_connection_status(
    context: ToolContext,
    arguments: GetExternalConnectionStatusArguments,
) -> ToolEnvelope:
    if arguments.account_id is not None:
        integration = await _scoped_integration(
            context, arguments.toolkit, arguments.account_id, active_only=False
        )
        integrations = [integration] if integration is not None else []
    else:
        integrations = await _integrations(context, arguments.toolkit)
    if not integrations:
        return _success(
            "integration_not_connected",
            f"{TOOLKIT_LABELS[arguments.toolkit]} ainda não está conectado.",
            {
                "toolkit": arguments.toolkit,
                "status": "not_connected",
                "connected": False,
            },
        )
    verified = 0
    for integration in integrations:
        try:
            remote_status = await _verified_remote_status(context, integration)
        except Exception:
            remote_status = None
        if remote_status is None:
            continue  # noqa: S112 - other accounts must still be verified
        integration.last_verified_at = datetime.now(UTC)
        integration.status = "active" if remote_status == "ACTIVE" else remote_status.casefold()
        verified += 1
    if verified == 0:
        return _failure(
            "integration_status_unavailable",
            "Não foi possível confirmar o estado das contas no provedor agora.",
            retryable=True,
        )
    connected = sum(item.status == "active" for item in integrations)
    return _success(
        "integration_status_checked",
        f"{connected} conta(s) {TOOLKIT_LABELS[arguments.toolkit]} estão conectadas.",
        {
            "toolkit": arguments.toolkit,
            "connected": connected > 0,
            "connected_count": connected,
            "accounts": [_account_data(item) for item in integrations],
        },
    )


async def list_external_accounts(
    context: ToolContext, arguments: ListExternalAccountsArguments
) -> ToolEnvelope:
    integrations = await _integrations(context, arguments.toolkit)
    return _success(
        "external_accounts_listed",
        f"Foram encontradas {len(integrations)} conexão(ões) externas.",
        {"accounts": [_account_data(item) for item in integrations]},
    )


async def configure_external_account(
    context: ToolContext, arguments: ConfigureExternalAccountArguments
) -> ToolEnvelope:
    integration = await context.session.scalar(
        select(ExternalIntegration).where(
            ExternalIntegration.id == arguments.account_id,
            ExternalIntegration.workspace_id == context.request_context.workspace_id,
            ExternalIntegration.user_id == context.request_context.identity.user_id,
        )
    )
    if integration is None:
        return _failure("external_account_not_found", "A conta externa não foi encontrada.")
    if arguments.account_label is not None:
        integration.account_label = arguments.account_label.strip()
    if arguments.set_as_primary:
        await context.session.execute(
            update(ExternalIntegration)
            .where(
                ExternalIntegration.workspace_id == context.request_context.workspace_id,
                ExternalIntegration.user_id == context.request_context.identity.user_id,
                ExternalIntegration.toolkit_slug == integration.toolkit_slug,
            )
            .values(is_default=False)
        )
        integration.is_default = True
    return _success(
        "external_account_configured",
        "As preferências da conta externa foram atualizadas.",
        _account_data(integration),
    )


async def connect_external_app(
    context: ToolContext, arguments: ConnectExternalAppArguments
) -> ToolEnvelope:
    toolkit = arguments.toolkit
    active_accounts = await _integrations(context, toolkit, active_only=True)
    active = active_accounts[0] if active_accounts else None
    if active is not None and not arguments.add_another:
        try:
            remote_status = await _verified_remote_status(context, active)
            if remote_status == "ACTIVE":
                active.last_verified_at = datetime.now(UTC)
                return _success(
                    "integration_already_connected",
                    f"{TOOLKIT_LABELS[toolkit]} já está conectado. "
                    "Se o usuário também pediu dados, prossiga usando a tool de leitura.",
                    {
                        "toolkit": toolkit,
                        "status": "active",
                        "connected": True,
                        "accounts": [_account_data(item) for item in active_accounts],
                    },
                )
            active.status = remote_status.casefold()
        except Exception:
            active.status = "verification_failed"
    state = secrets.token_urlsafe(32)
    integration_id = uuid.uuid4()
    gateway = ComposioGateway(context.settings)
    request = await gateway.create_link(
        user_id=opaque_user_id(context),
        auth_config_id=auth_config_id(context.settings, toolkit),
        callback_url=_callback_url(str(context.settings.composio_callback_url), state),
        alias=f"{toolkit}-{integration_id.hex[:16]}",
        allow_multiple=arguments.add_another,
    )
    connected_account_id = str(request.id)
    integration = ExternalIntegration(
        id=integration_id,
        workspace_id=context.request_context.workspace_id,
        user_id=context.request_context.identity.user_id,
        toolkit_slug=toolkit,
        auth_config_id=auth_config_id(context.settings, toolkit),
        connected_account_id=connected_account_id,
        status="pending",
        account_label=arguments.account_label.strip() if arguments.account_label else None,
        is_default=not active_accounts,
    )
    context.session.add(integration)
    await context.session.flush()
    context.session.add(
        ExternalConnectionRequest(
            workspace_id=context.request_context.workspace_id,
            user_id=context.request_context.identity.user_id,
            conversation_id=context.conversation.id,
            integration_id=integration.id,
            toolkit_slug=toolkit,
            auth_config_id=integration.auth_config_id,
            status="pending",
            composio_request_id=connected_account_id,
            callback_state_hash=hashlib.sha256(state.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
    )
    return _success(
        "integration_authorization_required",
        f"Abra o link para conectar sua conta {TOOLKIT_LABELS[toolkit]}.",
        {
            "toolkit": toolkit,
            "status": "pending",
            "authorization_url": str(request.redirect_url),
            "expires_in_seconds": 900,
            "account_id": str(integration.id),
            "account_label": integration.account_label,
            "adding_another": bool(active_accounts),
        },
    )


async def _execute_action(context: ToolContext, action: ExternalAction) -> ToolEnvelope:
    integration = await context.session.get(ExternalIntegration, action.integration_id)
    if integration is None or integration.status != "active":
        action.status = "failed"
        action.error_code = "external_connection_required"
        return _failure(
            "external_connection_required", "A conta externa precisa ser conectada novamente."
        )
    action.status = "executing"
    action.executed_at = datetime.now(UTC)
    await context.session.flush()
    try:
        payload, session_id = await ComposioGateway(context.settings).execute(
            user_id=opaque_user_id(context),
            toolkit=action.toolkit_slug,
            auth_config_id=integration.auth_config_id,
            connected_account_id=integration.connected_account_id,
            remote_slug=action.tool_slug,
            arguments=_decrypt(context, action.arguments_ciphertext),
        )
    except ComposioToolExecutionError as exc:
        action.status = "failed"
        action.error_code = "external_tool_rejected_arguments"
        action.completed_at = datetime.now(UTC)
        return _failure(
            "external_tool_rejected_arguments",
            f"O serviço conectado rejeitou os argumentos: {exc}",
            retryable=False,
        )
    except Exception:
        action.status = "failed"
        action.error_code = "external_execution_failed"
        action.completed_at = datetime.now(UTC)
        return _failure(
            "external_execution_failed",
            "Não foi possível concluir a ação no serviço conectado.",
            retryable=True,
        )
    bounded = bound_normalized_result(normalize_mcp_result(action.tool_slug, payload))
    integration.composio_session_id = session_id
    integration.last_verified_at = datetime.now(UTC)
    action.status = "completed"
    action.result_sanitized = bounded
    action.completed_at = datetime.now(UTC)
    return _success("external_action_completed", "A operação externa foi concluída.", bounded)


async def _execute_policy_for_integration(
    context: ToolContext,
    policy: ExternalToolPolicy,
    values: dict[str, Any],
    integration: ExternalIntegration,
) -> ToolEnvelope:
    arguments = remote_arguments(policy, values)
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    arguments_hash = hashlib.sha256(encoded.encode()).hexdigest()
    key = hashlib.sha256(
        f"{context.idempotency_key}:{integration.id}:{policy.remote_slug}:{arguments_hash}".encode()
    ).hexdigest()
    existing = await context.session.scalar(
        select(ExternalAction).where(
            ExternalAction.workspace_id == context.request_context.workspace_id,
            ExternalAction.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.status == "completed" and existing.result_sanitized is not None:
            return _success(
                "external_action_completed",
                "A operação externa já havia sido concluída.",
                existing.result_sanitized,
            )
        if existing.status in {"ready", "failed"} and policy.risk != "R2":
            return await _execute_action(context, existing)
        if existing.pending_action_id is not None:
            pending = await context.session.get(PendingAction, existing.pending_action_id)
            if pending is not None:
                return _success(
                    "confirmation_required",
                    "A ação externa ainda aguarda sua confirmação.",
                    {
                        "action_id": str(pending.id),
                        "summary": pending.summary,
                        "expires_at": pending.expires_at.isoformat(),
                    },
                )
    action = ExternalAction(
        workspace_id=context.request_context.workspace_id,
        user_id=context.request_context.identity.user_id,
        conversation_id=context.conversation.id,
        orchestration_task_id=context.orchestration_task.id if context.orchestration_task else None,
        integration_id=integration.id,
        toolkit_slug=policy.toolkit,
        tool_slug=policy.remote_slug,
        risk_level=policy.risk,
        arguments_sanitized=_redact(arguments),
        arguments_ciphertext=_encrypt(context, arguments),
        arguments_hash=arguments_hash,
        idempotency_key=key,
        status="proposed" if policy.risk == "R2" else "ready",
    )
    context.session.add(action)
    await context.session.flush()
    if policy.risk != "R2":
        return await _execute_action(context, action)
    from agents_backend.conversation.tools import create_pending_action

    pending = await create_pending_action(
        context,
        tool_name="external_action",
        arguments={"external_action_id": str(action.id)},
        summary=(
            f"Confirmar operação em {TOOLKIT_LABELS[policy.toolkit]} "
            f"({integration.account_label or integration.display_name or 'conta padrão'}): "
            f"{policy.description}"
        ),
    )
    action.pending_action_id = pending.id
    return _success(
        "confirmation_required",
        "A ação externa não foi executada. Confirme em uma nova mensagem para prosseguir.",
        {
            "action_id": str(pending.id),
            "summary": pending.summary,
            "expires_at": pending.expires_at.isoformat(),
        },
    )


def _merged_multi_account_read(
    policy: ExternalToolPolicy,
    values: dict[str, Any],
    results: list[tuple[ExternalIntegration, ToolEnvelope]],
) -> ToolEnvelope:
    successful = [(account, outcome) for account, outcome in results if outcome.ok]
    if not successful:
        return _failure(
            "external_multi_account_read_failed",
            "Não foi possível consultar nenhuma das contas conectadas.",
            retryable=any(outcome.retryable for _, outcome in results),
        )
    account_errors = [
        {
            "account": _account_data(account),
            "code": outcome.code,
            "message": outcome.message,
        }
        for account, outcome in results
        if not outcome.ok
    ]
    if policy.name in {"gmail_search_emails", "gmail_get_email"}:
        messages: list[dict[str, Any]] = []
        for account, outcome in successful:
            data = outcome.data if isinstance(outcome.data, dict) else {}
            raw_messages = data.get("messages")
            if not isinstance(raw_messages, list):
                continue
            for message in raw_messages:
                if isinstance(message, dict):
                    messages.append({**message, "account": _account_data(account)})
        messages.sort(key=lambda item: str(item.get("received_at") or ""), reverse=True)
        limit = int(values.get("max_results") or len(messages) or 1)
        return _success(
            "external_multi_account_read_completed",
            f"Foram consultadas {len(results)} contas Gmail.",
            {
                "provider": "gmail",
                "account_scope": "all_connected_accounts",
                "accounts_queried": len(results),
                "accounts_succeeded": len(successful),
                "count_returned": len(messages[:limit]),
                "messages": messages[:limit],
                "account_errors": account_errors,
            },
        )
    if policy.name == "calendar_list_events":
        events: list[dict[str, Any]] = []
        calendars: list[dict[str, Any]] = []
        for account, outcome in successful:
            data = outcome.data if isinstance(outcome.data, dict) else {}
            raw_events = data.get("events")
            raw_calendars = data.get("calendars_queried")
            if isinstance(raw_events, list):
                for event in raw_events:
                    if isinstance(event, dict):
                        events.append({**event, "account": _account_data(account)})
            if isinstance(raw_calendars, list):
                calendars.extend(
                    {**calendar, "account": _account_data(account)}
                    for calendar in raw_calendars
                    if isinstance(calendar, dict)
                )
        events.sort(key=lambda item: str(item.get("start") or ""))
        return _success(
            "external_multi_account_read_completed",
            f"Foram consultadas {len(results)} contas Google Calendar.",
            {
                "provider": "googlecalendar",
                "account_scope": "all_connected_accounts",
                "accounts_queried": len(results),
                "accounts_succeeded": len(successful),
                "count_returned": len(events),
                "calendars_queried": calendars,
                "events": events,
                "account_errors": account_errors,
            },
        )
    return _success(
        "external_multi_account_read_completed",
        f"Foram consultadas {len(results)} contas conectadas.",
        {
            "account_scope": "all_connected_accounts",
            "results": [
                {"account": _account_data(account), "data": outcome.data}
                for account, outcome in successful
            ],
            "account_errors": account_errors,
        },
    )


async def execute_policy(
    context: ToolContext, policy: ExternalToolPolicy, values: dict[str, Any]
) -> ToolEnvelope:
    selected_values = dict(values)
    raw_account_id = selected_values.pop("account_id", None)
    if raw_account_id is not None:
        try:
            account_id = uuid.UUID(str(raw_account_id))
        except ValueError:
            return _failure("external_account_not_found", "A conta externa indicada é inválida.")
        integration = await _scoped_integration(context, policy.toolkit, account_id)
        integrations = [integration] if integration is not None else []
    else:
        active = await _integrations(context, policy.toolkit, active_only=True)
        if policy.risk == "R0" and policy.toolkit in {"gmail", "googlecalendar"}:
            integrations = active
        else:
            integrations = active[:1]
    if not integrations:
        return _failure(
            "external_connection_required",
            f"A conta {TOOLKIT_LABELS[policy.toolkit]} ainda não está conectada. "
            "Use a autorização de conta antes de tentar novamente.",
        )
    results = [
        (
            integration,
            await _execute_policy_for_integration(context, policy, selected_values, integration),
        )
        for integration in integrations
    ]
    if len(results) == 1:
        return results[0][1]
    return _merged_multi_account_read(policy, selected_values, results)


async def execute_pending_external_action(
    context: ToolContext, pending: PendingAction
) -> ToolEnvelope:
    action = await context.session.get(
        ExternalAction, uuid.UUID(str(pending.arguments["external_action_id"]))
    )
    if action is None or action.workspace_id != context.request_context.workspace_id:
        return _failure("external_action_not_found", "A ação externa pendente não foi encontrada.")
    action.confirmed_at = datetime.now(UTC)
    return await _execute_action(context, action)


def composio_tool_specs(capabilities: list[str]) -> list[ToolSpec]:
    if not {
        "integration_connection",
        "integration_read",
        "integration_draft",
        "integration_execute",
    }.intersection(capabilities):
        return []
    specs: list[ToolSpec] = []
    if "integration_connection" in capabilities:
        specs.extend(
            [
                ToolSpec(
                    "connect_external_app",
                    "Gera um link privado para conectar Gmail, Google Calendar ou WhatsApp "
                    "Business. Quando o usuário pedir outra conta do mesmo serviço, use "
                    "add_another=true e opcionalmente dê um account_label.",
                    ConnectExternalAppArguments,
                    "R1",
                    connect_external_app,
                ),
                ToolSpec(
                    "get_external_connection_status",
                    "Confere no Composio se Gmail, Google Calendar ou WhatsApp Business "
                    "do usuário está conectado. Não lê conteúdo da conta.",
                    GetExternalConnectionStatusArguments,
                    "R0",
                    get_external_connection_status,
                ),
                ToolSpec(
                    "list_external_accounts",
                    "Lista contas externas, account_id, apelidos, estados e qual é a padrão. "
                    "Use antes de escolher uma conta específica.",
                    ListExternalAccountsArguments,
                    "R0",
                    list_external_accounts,
                ),
                ToolSpec(
                    "configure_external_account",
                    "Define um apelido e/ou torna uma conta externa a padrão do serviço.",
                    ConfigureExternalAccountArguments,
                    "R1",
                    configure_external_account,
                ),
            ]
        )
    for policy in POLICIES:
        if policy.capability not in capabilities:
            continue

        async def handler(
            context: ToolContext, arguments: Any, selected: ExternalToolPolicy = policy
        ) -> ToolEnvelope:
            return await execute_policy(context, selected, arguments.model_dump(mode="json"))

        specs.append(
            ToolSpec(policy.name, policy.description, policy.arguments_model, policy.risk, handler)
        )
    return specs
