from __future__ import annotations

from agents_backend.config import Settings
from agents_backend.models import Commitment, Entity, Fact
from agents_backend.profile.service import get_user_context_profile


def profile_settings(**overrides: str) -> Settings:
    values = {
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_ANON_KEY": "anon",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        "DATABASE_URL": "postgresql://postgres:password@db.project.supabase.co/postgres",
        "OPENAI_API_KEY": "openai-test",
        "OPENAI_MODEL_EXTRACTION": "gpt-5.6-luna",
        "OPENAI_MODEL_ANSWERING": "gpt-5.6-luna",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


async def test_profile_contains_only_active_current_and_open_memory(session, context) -> None:
    ana = Entity(
        workspace_id=context.workspace_id,
        entity_type="person",
        canonical_name="Ana",
        canonical_name_normalized="ana",
        aliases=[],
        status="active",
    )
    archived = Entity(
        workspace_id=context.workspace_id,
        entity_type="project",
        canonical_name="Arquivo",
        canonical_name_normalized="arquivo",
        aliases=[],
        status="deleted",
    )
    session.add_all([ana, archived])
    await session.flush()
    session.add_all(
        [
            Fact(
                workspace_id=context.workspace_id,
                subject_entity_id=ana.id,
                fact_type="decision",
                predicate="responsible_for_review",
                value={"value": "Ana revisa a wiki."},
                value_text="Ana revisa a wiki.",
                fingerprint="1" * 64,
                status="current",
                confidence=1.0,
            ),
            Fact(
                workspace_id=context.workspace_id,
                fact_type="decision",
                predicate="old_decision",
                value={"value": "Decisão removida."},
                value_text="Decisão removida.",
                fingerprint="2" * 64,
                status="deleted",
                confidence=1.0,
            ),
            Commitment(
                workspace_id=context.workspace_id,
                responsible_entity_id=ana.id,
                description="Revisar o conteúdo até sexta-feira.",
                fingerprint="3" * 64,
                status="open",
                confidence=1.0,
            ),
            Commitment(
                workspace_id=context.workspace_id,
                description="Tarefa concluída.",
                fingerprint="4" * 64,
                status="completed",
                confidence=1.0,
            ),
        ]
    )
    await session.commit()

    profile = await get_user_context_profile(
        session,
        context,
        settings=profile_settings(PROFILE_CONTEXT_MAX_CHARACTERS="800"),
    )

    assert "Ana (person)" in profile.summary
    assert "Ana revisa a wiki." in profile.summary
    assert "Revisar o conteúdo até sexta-feira." in profile.summary
    assert "Decisão removida." not in profile.summary
    assert "Tarefa concluída." not in profile.summary
    assert "Arquivo" not in profile.summary


async def test_profile_respects_context_character_budget(session, context) -> None:
    session.add(
        Fact(
            workspace_id=context.workspace_id,
            fact_type="note",
            predicate="long_note",
            value={"value": "x" * 600},
            value_text="x" * 600,
            fingerprint="5" * 64,
            status="current",
            confidence=1.0,
        )
    )
    await session.commit()

    profile = await get_user_context_profile(
        session,
        context,
        settings=profile_settings(PROFILE_CONTEXT_MAX_CHARACTERS="400"),
    )

    assert len(profile.summary) <= 400
