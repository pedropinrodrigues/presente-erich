"""Add internal identities, platform admins and Telegram invitations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0009"
down_revision: str | None = "20260824_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("locale", sa.String(30), nullable=True),
        sa.Column("timezone", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_subject", sa.String(200), nullable=False),
        sa.Column("identity_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_user_identity_provider"
        ),
    )
    op.create_index(
        "ix_user_identities_user", "user_identities", ["user_id", "provider"]
    )
    op.create_table(
        "platform_admins",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "channel_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("accepted_provider_subject", sa.String(200), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_channel_invite_token_hash"),
    )
    op.create_index(
        "ix_channel_invites_status_expiry", "channel_invites", ["status", "expires_at"]
    )
    op.create_index(
        "ix_channel_invites_creator",
        "channel_invites",
        ["created_by_user_id", "created_at"],
    )

    op.execute(
        """
        INSERT INTO app_users (id, status, created_at, updated_at)
        SELECT owner_user_id, 'active', MIN(created_at), NOW()
        FROM workspaces
        GROUP BY owner_user_id
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO user_identities (
            id, user_id, provider, provider_subject, identity_metadata, verified_at, created_at
        )
        SELECT gen_random_uuid(), id, 'supabase', id::text, '{}'::jsonb, created_at, created_at
        FROM app_users
        ON CONFLICT (provider, provider_subject) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO user_identities (
            id, user_id, provider, provider_subject, identity_metadata, verified_at, created_at
        )
        SELECT
            gen_random_uuid(),
            account.user_id,
            'telegram',
            COALESCE(
                (
                    SELECT message.message_metadata->>'telegram_user_id'
                    FROM conversations conversation
                    JOIN channel_messages message
                      ON message.conversation_id = conversation.id
                    WHERE conversation.channel_account_id = account.id
                      AND message.direction = 'inbound'
                      AND message.message_metadata->>'telegram_user_id' IS NOT NULL
                    ORDER BY message.created_at DESC
                    LIMIT 1
                ),
                account.external_account_id
            ),
            '{}'::jsonb,
            COALESCE(account.verified_at, account.created_at),
            account.created_at
        FROM channel_accounts account
        WHERE account.provider = 'telegram' AND account.active = TRUE
        ON CONFLICT (provider, provider_subject) DO NOTHING
        """
    )
    op.create_foreign_key(
        "fk_workspaces_owner_app_user",
        "workspaces",
        "app_users",
        ["owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_channel_accounts_user_app_user",
        "channel_accounts",
        "app_users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.accept_telegram_invite(
            p_token_hash text,
            p_chat_id text,
            p_telegram_user_id text,
            p_profile_metadata jsonb DEFAULT '{}'::jsonb
        )
        RETURNS TABLE (
            result_code text,
            resolved_user_id uuid,
            resolved_workspace_id uuid,
            resolved_channel_account_id uuid
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            selected_invite public.channel_invites%ROWTYPE;
            selected_user_id uuid;
            selected_workspace_id uuid;
            selected_channel_account_id uuid;
            selected_result text;
        BEGIN
            SELECT * INTO selected_invite
            FROM public.channel_invites
            WHERE token_hash = p_token_hash
            FOR UPDATE;

            IF NOT FOUND THEN
                RETURN QUERY SELECT 'unavailable'::text, NULL::uuid, NULL::uuid, NULL::uuid;
                RETURN;
            END IF;

            IF selected_invite.status = 'accepted' THEN
                IF selected_invite.accepted_provider_subject = p_telegram_user_id THEN
                    SELECT workspace.id INTO selected_workspace_id
                    FROM public.workspaces workspace
                    WHERE workspace.owner_user_id = selected_invite.accepted_by_user_id;
                    SELECT account.id INTO selected_channel_account_id
                    FROM public.channel_accounts account
                    WHERE account.provider = 'telegram'
                      AND account.external_account_id = p_chat_id;
                    RETURN QUERY SELECT
                        'already_accepted_by_same_identity'::text,
                        selected_invite.accepted_by_user_id,
                        selected_workspace_id,
                        selected_channel_account_id;
                ELSE
                    RETURN QUERY SELECT 'unavailable'::text, NULL::uuid, NULL::uuid, NULL::uuid;
                END IF;
                RETURN;
            END IF;

            IF selected_invite.status = 'revoked' THEN
                RETURN QUERY SELECT 'revoked'::text, NULL::uuid, NULL::uuid, NULL::uuid;
                RETURN;
            END IF;
            IF selected_invite.status = 'expired' OR selected_invite.expires_at <= NOW() THEN
                UPDATE public.channel_invites
                SET status = 'expired'
                WHERE id = selected_invite.id AND status = 'pending';
                RETURN QUERY SELECT 'expired'::text, NULL::uuid, NULL::uuid, NULL::uuid;
                RETURN;
            END IF;
            IF selected_invite.status <> 'pending' THEN
                RETURN QUERY SELECT 'unavailable'::text, NULL::uuid, NULL::uuid, NULL::uuid;
                RETURN;
            END IF;

            SELECT identity.user_id INTO selected_user_id
            FROM public.user_identities identity
            WHERE identity.provider = 'telegram'
              AND identity.provider_subject = p_telegram_user_id;

            IF selected_user_id IS NULL THEN
                selected_user_id := gen_random_uuid();
                INSERT INTO public.app_users (
                    id, display_name, locale, timezone, status, created_at, updated_at
                ) VALUES (
                    selected_user_id,
                    NULLIF(p_profile_metadata->>'first_name', ''),
                    NULLIF(p_profile_metadata->>'language_code', ''),
                    NULL,
                    'active',
                    NOW(),
                    NOW()
                );
                INSERT INTO public.user_identities (
                    id, user_id, provider, provider_subject, identity_metadata,
                    verified_at, created_at
                ) VALUES (
                    gen_random_uuid(), selected_user_id, 'telegram', p_telegram_user_id,
                    COALESCE(p_profile_metadata, '{}'::jsonb), NOW(), NOW()
                );
                selected_result := 'created';
            ELSE
                selected_result := 'already_registered';
            END IF;

            SELECT workspace.id INTO selected_workspace_id
            FROM public.workspaces workspace
            WHERE workspace.owner_user_id = selected_user_id;
            IF selected_workspace_id IS NULL THEN
                selected_workspace_id := gen_random_uuid();
                INSERT INTO public.workspaces (
                    id, owner_user_id, created_at, updated_at
                ) VALUES (selected_workspace_id, selected_user_id, NOW(), NOW());
            END IF;

            INSERT INTO public.channel_accounts (
                id, workspace_id, user_id, provider, external_account_id, display_name,
                verified_at, verification_code_hash, verification_expires_at, active,
                created_at, updated_at
            ) VALUES (
                gen_random_uuid(), selected_workspace_id, selected_user_id, 'telegram', p_chat_id,
                NULLIF(p_profile_metadata->>'first_name', ''), NOW(), NULL, NULL, TRUE, NOW(), NOW()
            )
            ON CONFLICT (provider, external_account_id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                user_id = EXCLUDED.user_id,
                display_name = COALESCE(EXCLUDED.display_name, channel_accounts.display_name),
                verified_at = NOW(),
                verification_code_hash = NULL,
                verification_expires_at = NULL,
                active = TRUE,
                updated_at = NOW()
            RETURNING id INTO selected_channel_account_id;

            UPDATE public.channel_invites
            SET status = 'accepted',
                accepted_by_user_id = selected_user_id,
                accepted_provider_subject = p_telegram_user_id,
                accepted_at = NOW()
            WHERE id = selected_invite.id;

            INSERT INTO public.audit_events (
                id, workspace_id, actor_user_id, operation, target_type, target_id,
                reason, event_metadata, created_at
            ) VALUES (
                gen_random_uuid(), selected_workspace_id, selected_user_id,
                'invite_accepted', 'channel_invite', selected_invite.id,
                NULL, jsonb_build_object('result_code', selected_result), NOW()
            );

            RETURN QUERY SELECT
                selected_result,
                selected_user_id,
                selected_workspace_id,
                selected_channel_account_id;
        END;
        $$;
        """
    )
    op.execute(
        """
        REVOKE ALL ON FUNCTION public.accept_telegram_invite(text, text, text, jsonb)
            FROM PUBLIC, anon, authenticated
        """
    )
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION public.accept_telegram_invite(text, text, text, jsonb)
            TO service_role
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS public.accept_telegram_invite(text, text, text, jsonb)"
    )
    op.drop_constraint(
        "fk_channel_accounts_user_app_user", "channel_accounts", type_="foreignkey"
    )
    op.drop_constraint("fk_workspaces_owner_app_user", "workspaces", type_="foreignkey")
    op.drop_index("ix_channel_invites_creator", table_name="channel_invites")
    op.drop_index("ix_channel_invites_status_expiry", table_name="channel_invites")
    op.drop_table("channel_invites")
    op.drop_table("platform_admins")
    op.drop_index("ix_user_identities_user", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_table("app_users")
