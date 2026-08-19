from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_backend.auth import RequestContext
from agents_backend.config import Settings, get_settings
from agents_backend.models import Commitment, CommitmentStatus, Entity, Fact, FactStatus
from agents_backend.schemas import ContextProfileItem, UserContextProfileResponse


def _clip(value: str, maximum: int) -> str:
    clean = " ".join(value.split())
    if len(clean) <= maximum:
        return clean
    return f"{clean[: maximum - 1].rstrip()}…"


def _append_with_budget(lines: list[str], line: str, maximum: int) -> bool:
    candidate = "\n".join([*lines, line])
    if len(candidate) > maximum:
        return False
    lines.append(line)
    return True


async def get_user_context_profile(
    session: AsyncSession,
    context: RequestContext,
    *,
    settings: Settings | None = None,
) -> UserContextProfileResponse:
    """Build a bounded, auditable conversation aid from active workspace memory."""

    configuration = settings or get_settings()
    item_limit = configuration.profile_context_max_items
    entities = list(
        (
            await session.scalars(
                select(Entity)
                .where(Entity.workspace_id == context.workspace_id, Entity.status == "active")
                .order_by(Entity.updated_at.desc(), Entity.id.desc())
                .limit(item_limit)
            )
        ).all()
    )
    facts = list(
        (
            await session.execute(
                select(Fact, Entity.canonical_name)
                .outerjoin(Entity, Entity.id == Fact.subject_entity_id)
                .where(
                    Fact.workspace_id == context.workspace_id,
                    Fact.status == FactStatus.CURRENT.value,
                )
                .order_by(Fact.updated_at.desc(), Fact.id.desc())
                .limit(item_limit)
            )
        ).all()
    )
    commitments = list(
        (
            await session.execute(
                select(Commitment, Entity.canonical_name)
                .outerjoin(Entity, Entity.id == Commitment.responsible_entity_id)
                .where(
                    Commitment.workspace_id == context.workspace_id,
                    Commitment.status == CommitmentStatus.OPEN.value,
                )
                .order_by(Commitment.updated_at.desc(), Commitment.id.desc())
                .limit(item_limit)
            )
        ).all()
    )

    entity_items = [
        ContextProfileItem(
            label=entity.canonical_name,
            detail=entity.entity_type,
            updated_at=entity.updated_at,
        )
        for entity in entities
    ]
    fact_items = [
        ContextProfileItem(
            label=(f"{subject}: {fact.predicate}" if subject else fact.predicate),
            detail=_clip(fact.value_text, 360),
            updated_at=fact.updated_at,
        )
        for fact, subject in facts
    ]
    commitment_items = [
        ContextProfileItem(
            label=(f"{responsible}: compromisso" if responsible else "Compromisso"),
            detail=_clip(commitment.description, 360),
            updated_at=commitment.updated_at,
        )
        for commitment, responsible in commitments
    ]

    lines = [
        "Perfil contextual derivado da wiki. Não é fonte de verdade; use-o apenas para "
        "interpretar referências e formular buscas.",
    ]
    if entity_items:
        _append_with_budget(
            lines,
            "Entidades relevantes: "
            + ", ".join(f"{item.label} ({item.detail})" for item in entity_items),
            configuration.profile_context_max_characters,
        )
    for item in fact_items:
        if not _append_with_budget(
            lines,
            f"Fato atual — {item.label}: {item.detail}",
            configuration.profile_context_max_characters,
        ):
            break
    for item in commitment_items:
        if not _append_with_budget(
            lines,
            f"Compromisso aberto — {item.label}: {item.detail}",
            configuration.profile_context_max_characters,
        ):
            break

    updated_values: list[datetime] = [
        item.updated_at for item in [*entity_items, *fact_items, *commitment_items]
    ]
    latest_update = None
    if updated_values:
        latest_update = max(updated_values, key=lambda value: value.timestamp())
    return UserContextProfileResponse(
        summary="\n".join(lines),
        entities=entity_items,
        current_facts=fact_items,
        open_commitments=commitment_items,
        updated_at=latest_update,
    )
