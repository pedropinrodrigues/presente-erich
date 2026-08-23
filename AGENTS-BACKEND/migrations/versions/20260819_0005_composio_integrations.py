"""Composio connections and auditable external actions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0005"
down_revision: str | None = "20260818_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("toolkit_slug", sa.String(100), nullable=False),
        sa.Column("auth_config_id", sa.String(100), nullable=False),
        sa.Column("connected_account_id", sa.String(100), nullable=False),
        sa.Column("composio_session_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("display_name", sa.String(300), nullable=True),
        sa.Column("integration_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "provider",
            "connected_account_id",
            name="uq_external_integration_account",
        ),
    )
    op.create_index(
        "ix_external_integrations_scope",
        "external_integrations",
        ["workspace_id", "user_id", "toolkit_slug", "status"],
    )

    op.create_table(
        "external_connection_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_integrations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("toolkit_slug", sa.String(100), nullable=False),
        sa.Column("auth_config_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("composio_request_id", sa.String(100), nullable=False),
        sa.Column("callback_state_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", "callback_state_hash", name="uq_external_connection_state"
        ),
    )
    op.create_index(
        "ix_external_connection_requests_scope",
        "external_connection_requests",
        ["workspace_id", "user_id", "status", "expires_at"],
    )

    op.create_table(
        "external_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "orchestration_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_integrations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "pending_action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pending_actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("toolkit_slug", sa.String(100), nullable=False),
        sa.Column("tool_slug", sa.String(150), nullable=False),
        sa.Column("risk_level", sa.String(10), nullable=False),
        sa.Column("arguments_sanitized", sa.JSON(), nullable=False),
        sa.Column("arguments_ciphertext", sa.Text(), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("composio_execution_id", sa.String(150), nullable=True),
        sa.Column("result_sanitized", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_external_action_key"
        ),
    )
    op.create_index(
        "ix_external_actions_task",
        "external_actions",
        ["orchestration_task_id", "created_at"],
    )

    op.add_column("tool_executions", sa.Column("provider", sa.String(30), nullable=True))
    op.add_column(
        "tool_executions",
        sa.Column("external_action_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tool_executions_external_action",
        "tool_executions",
        "external_actions",
        ["external_action_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "tool_executions", sa.Column("external_execution_id", sa.String(150), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tool_executions", "external_execution_id")
    op.drop_constraint(
        "fk_tool_executions_external_action", "tool_executions", type_="foreignkey"
    )
    op.drop_column("tool_executions", "external_action_id")
    op.drop_column("tool_executions", "provider")
    op.drop_index("ix_external_actions_task", table_name="external_actions")
    op.drop_table("external_actions")
    op.drop_index(
        "ix_external_connection_requests_scope", table_name="external_connection_requests"
    )
    op.drop_table("external_connection_requests")
    op.drop_index("ix_external_integrations_scope", table_name="external_integrations")
    op.drop_table("external_integrations")
