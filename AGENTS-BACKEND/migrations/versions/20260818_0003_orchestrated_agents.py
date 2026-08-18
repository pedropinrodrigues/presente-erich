"""Persisted orchestration tasks, separated agent runs and ordered outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260818_0003"
down_revision: str | None = "20260816_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_messages",
        sa.Column("depends_on_outbox_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_outbox_messages_depends_on",
        "outbox_messages",
        "outbox_messages",
        ["depends_on_outbox_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "orchestration_tasks",
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
            "inbound_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("intent", sa.String(50), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("allowed_capabilities", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(100), nullable=True),
        sa.Column(
            "ack_outbox_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outbox_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "result_outbox_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outbox_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result_code", sa.String(100), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_orchestration_task_workspace_key",
        ),
        sa.UniqueConstraint(
            "inbound_message_id", name="uq_orchestration_task_inbound_message"
        ),
    )
    op.create_index(
        "ix_orchestration_tasks_claim",
        "orchestration_tasks",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_orchestration_tasks_conversation",
        "orchestration_tasks",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "orchestration_task_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "orchestration_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_orchestration_task_events_task",
        "orchestration_task_events",
        ["orchestration_task_id", "created_at"],
    )

    op.drop_constraint("uq_agent_run_inbound_message", "agent_runs", type_="unique")
    op.add_column(
        "agent_runs",
        sa.Column("run_type", sa.String(30), server_default="conversation", nullable=False),
    )
    op.add_column(
        "agent_runs",
        sa.Column("orchestration_task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_orchestration_task",
        "agent_runs",
        "orchestration_tasks",
        ["orchestration_task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_agent_run_type_inbound_message",
        "agent_runs",
        ["run_type", "inbound_message_id"],
    )
    op.create_index(
        "ix_agent_runs_orchestration_task",
        "agent_runs",
        ["orchestration_task_id", "created_at"],
    )

    for table, ondelete in (
        ("tool_executions", "CASCADE"),
        ("pending_actions", "SET NULL"),
    ):
        op.add_column(
            table,
            sa.Column("orchestration_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_orchestration_task",
            table,
            "orchestration_tasks",
            ["orchestration_task_id"],
            ["id"],
            ondelete=ondelete,
        )


def downgrade() -> None:
    for table in ("pending_actions", "tool_executions"):
        op.drop_constraint(f"fk_{table}_orchestration_task", table, type_="foreignkey")
        op.drop_column(table, "orchestration_task_id")

    op.drop_index("ix_agent_runs_orchestration_task", table_name="agent_runs")
    op.drop_constraint(
        "uq_agent_run_type_inbound_message", "agent_runs", type_="unique"
    )
    op.drop_constraint(
        "fk_agent_runs_orchestration_task", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "orchestration_task_id")
    op.drop_column("agent_runs", "run_type")
    op.create_unique_constraint(
        "uq_agent_run_inbound_message", "agent_runs", ["inbound_message_id"]
    )

    op.drop_table("orchestration_task_events")
    op.drop_table("orchestration_tasks")
    op.drop_constraint(
        "fk_outbox_messages_depends_on", "outbox_messages", type_="foreignkey"
    )
    op.drop_column("outbox_messages", "depends_on_outbox_id")
