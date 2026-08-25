from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings
from agents_backend.errors import AppError
from agents_backend.invitations.service import (
    create_telegram_invite,
    get_my_account,
    list_telegram_invites,
    revoke_telegram_invite,
)


async def handle_account_command(
    session: AsyncSession,
    context: RequestContext,
    message: str,
    settings: Settings,
) -> str | None:
    parts = message.strip().split(maxsplit=1)
    command = parts[0].split("@", maxsplit=1)[0].casefold() if parts else ""
    if command not in {"/convidar", "/convites", "/revogar", "/minhaconta"}:
        return None
    try:
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
