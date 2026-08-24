"""Scheduled automations, runs, grants and audit events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0007"
down_revision: str | None = "20260823_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_automations",
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
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("original_request", sa.Text(), nullable=False),
        sa.Column("compiled_spec", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("recurrence_rule", sa.String(1000), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("misfire_policy", sa.String(20), nullable=False),
        sa.Column("misfire_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("max_runs", sa.Integer(), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("capabilities_snapshot", sa.JSON(), nullable=False),
        sa.Column("tool_policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_scheduled_automations_due",
        "scheduled_automations",
        ["status", "next_run_at"],
    )
    op.create_index(
        "ix_scheduled_automations_scope",
        "scheduled_automations",
        ["workspace_id", "user_id", "created_at"],
    )

    op.create_table(
        "scheduled_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scheduled_automation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduled_automations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("automation_revision", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(100), nullable=True),
        sa.Column(
            "orchestration_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result_code", sa.String(100), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "scheduled_automation_id", "scheduled_for", name="uq_scheduled_run_occurrence"
        ),
    )
    op.create_index(
        "ix_scheduled_runs_claim",
        "scheduled_runs",
        ["status", "available_at", "scheduled_for"],
    )
    op.create_index(
        "ix_scheduled_runs_automation",
        "scheduled_runs",
        ["scheduled_automation_id", "created_at"],
    )

    op.create_table(
        "automation_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scheduled_automation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduled_automations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("automation_revision", sa.Integer(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("allowed_account_ids", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("max_risk", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "confirmed_by_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "scheduled_automation_id",
            "automation_revision",
            name="uq_automation_grant_revision",
        ),
    )
    op.create_index(
        "ix_automation_grants_scope",
        "automation_grants",
        ["workspace_id", "user_id", "status"],
    )

    op.create_table(
        "schedule_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scheduled_automation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduled_automations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scheduled_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduled_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_schedule_events_automation",
        "schedule_events",
        ["scheduled_automation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_events_automation", table_name="schedule_events")
    op.drop_table("schedule_events")
    op.drop_index("ix_automation_grants_scope", table_name="automation_grants")
    op.drop_table("automation_grants")
    op.drop_index("ix_scheduled_runs_automation", table_name="scheduled_runs")
    op.drop_index("ix_scheduled_runs_claim", table_name="scheduled_runs")
    op.drop_table("scheduled_runs")
    op.drop_index("ix_scheduled_automations_scope", table_name="scheduled_automations")
    op.drop_index("ix_scheduled_automations_due", table_name="scheduled_automations")
    op.drop_table("scheduled_automations")
