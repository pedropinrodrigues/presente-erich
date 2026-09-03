from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.accounts.admin_service import (
    disable_account,
    get_managed_account,
    list_accounts,
    reactivate_account,
)
from agents_backend.auth import RequestContext
from agents_backend.config import Settings
from agents_backend.errors import AppError
from agents_backend.integrations.macwhisper.service import (
    create_webhook_credential,
    revoke_webhook_credential,
)
from agents_backend.invitations.service import (
    create_telegram_invite,
    get_my_account,
    list_telegram_invites,
    revoke_telegram_invite,
)


def _command_name(message: str) -> str:
    parts = message.strip().split(maxsplit=1)
    return parts[0].split("@", maxsplit=1)[0].casefold() if parts else ""


def response_contains_sensitive_credential(message: str, response: str | None) -> bool:
    return (
        _command_name(message) == "/macwhisper"
        and response is not None
        and "/integrations/macwhisper/webhooks/" in response
    )


def _account_label(display_name: str | None, user_id: uuid.UUID) -> str:
    return display_name.strip() if display_name and display_name.strip() else str(user_id)


def _status_label(status: str) -> str:
    return {"active": "ativa", "disabled": "desativada", "deleted": "excluída"}.get(status, status)


async def handle_account_command(
    session: AsyncSession,
    context: RequestContext,
    message: str,
    settings: Settings,
    *,
    provider: str | None = None,
) -> str | None:
    parts = message.strip().split(maxsplit=1)
    command = _command_name(message)
    if command not in {
        "/convidar",
        "/convites",
        "/revogar",
        "/minhaconta",
        "/macwhisper",
        "/revogarmacwhisper",
        "/contas",
        "/desativarconta",
        "/reativarconta",
    }:
        return None
    try:
        if command in {"/macwhisper", "/revogarmacwhisper"}:
            if provider != "telegram":
                return "Por segurança, configure o MacWhisper pelo chat privado do Telegram."
            if not settings.macwhisper_webhook_enabled:
                return "A integração MacWhisper ainda não está habilitada neste ambiente."
            if command == "/revogarmacwhisper":
                revoked = await revoke_webhook_credential(session, context)
                return (
                    "A URL pessoal do MacWhisper foi revogada. Envie /macwhisper para gerar outra."
                    if revoked
                    else "Você não possui uma URL MacWhisper ativa."
                )
            credential = await create_webhook_credential(session, context, settings)
            if not credential.created:
                return (
                    "Você já possui uma URL MacWhisper ativa. Por segurança, ela não pode ser "
                    "exibida novamente. Envie /revogarmacwhisper e depois /macwhisper para trocar."
                )
            return (
                "URL pessoal do MacWhisper criada.\n\n"
                f"{credential.webhook_url}\n\n"
                "Cole em MacWhisper → Settings → Integrations → Custom Webhook e toque em Test. "
                "Esta URL funciona como senha: não compartilhe."
            )
        if command == "/contas":
            accounts = await list_accounts(session, context, settings)
            lines = [f"Contas da plataforma ({len(accounts)}):"]
            for account in accounts:
                name = _account_label(account.display_name, account.user_id)
                admin = " — administradora" if account.platform_admin else ""
                lines.append(
                    f"• {name} — {_status_label(account.status)}{admin}\n  ID: {account.user_id}"
                )
            return "\n".join(lines)
        if command in {"/desativarconta", "/reativarconta"}:
            arguments = parts[1].split() if len(parts) == 2 else []
            usage = (
                "Use /desativarconta ID_DA_CONTA."
                if command == "/desativarconta"
                else "Use /reativarconta ID_DA_CONTA."
            )
            expected_lengths = {1, 2} if command == "/desativarconta" else {1}
            if len(arguments) not in expected_lengths:
                return usage
            try:
                target_user_id = uuid.UUID(arguments[0])
            except ValueError:
                return f"ID de conta inválido. Consulte /contas.\n\n{usage}"
            if command == "/reativarconta":
                account = await reactivate_account(session, context, target_user_id, settings)
                return (
                    f"Conta reativada: {_account_label(account.display_name, account.user_id)}.\n"
                    f"ID: {account.user_id}"
                )
            if len(arguments) == 1:
                account = await get_managed_account(session, context, target_user_id, settings)
                if account.user_id == context.identity.user_id:
                    return "Você não pode desativar sua própria conta."
                if account.platform_admin:
                    return "Outra conta administradora não pode ser desativada por este comando."
                if account.status == "disabled":
                    return "A conta já está desativada."
                return (
                    "Confirme a suspensão desta conta:\n"
                    f"• {_account_label(account.display_name, account.user_id)}\n"
                    f"• ID: {account.user_id}\n\n"
                    "Nenhum dado será apagado. Para confirmar, envie exatamente:\n"
                    f"/desativarconta {account.user_id} confirmar"
                )
            if arguments[1].casefold() != "confirmar":
                return usage
            account = await disable_account(session, context, target_user_id, settings)
            return (
                f"Conta desativada: {_account_label(account.display_name, account.user_id)}.\n"
                f"ID: {account.user_id}\n"
                "O acesso foi suspenso e pode ser restaurado com /reativarconta."
            )
        if command == "/convidar":
            invite = await create_telegram_invite(session, context, settings)
            return (
                "Convite criado.\n\n"
                "Quem abrir este link receberá uma conta pessoal separada da sua. "
                f"O convite pode ser usado uma vez e expira em {settings.invite_ttl_hours} horas."
                f"\n\n{invite.deep_link}"
            )
        if command == "/convites":
            invites = await list_telegram_invites(session, context, settings=settings)
            if not invites:
                return "Você ainda não criou convites."
            lines = ["Seus convites:"]
            for invite in invites[:20]:
                lines.append(
                    f"• {invite.invite_id} — {invite.status} — "
                    f"expira {invite.expires_at.isoformat()}"
                )
            return "\n".join(lines)
        if command == "/revogar":
            if len(parts) != 2:
                return "Use /revogar seguido do ID mostrado em /convites."
            try:
                invite_id = uuid.UUID(parts[1].strip())
            except ValueError:
                return "O ID do convite é inválido. Consulte /convites e tente novamente."
            invite = await revoke_telegram_invite(session, context, invite_id, settings)
            return f"Convite {invite.invite_id} revogado."
        account = await get_my_account(session, context, settings)
        admin_text = "sim" if account.platform_admin else "não"
        return (
            "Minha conta\n\n"
            f"ID: {account.user_id}\n"
            f"Status: {account.status}\n"
            f"Administrador da plataforma: {admin_text}\n"
            f"Política de convites: {account.invitation_policy}"
        )
    except AppError as exc:
        await session.rollback()
        return exc.message
