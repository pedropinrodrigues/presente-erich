from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

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
