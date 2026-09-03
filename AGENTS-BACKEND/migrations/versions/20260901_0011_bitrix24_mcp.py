"""Add secure credentials and confirmation context for Bitrix24 MCP."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0011"
down_revision: str | None = "20260825_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "external_integrations", sa.Column("credential_ciphertext", sa.Text(), nullable=True)
    )
    op.add_column(
        "external_integrations", sa.Column("credential_fingerprint", sa.String(64), nullable=True)
    )
    op.add_column(
        "external_integrations", sa.Column("credential_kind", sa.String(30), nullable=True)
    )
    op.add_column(
        "external_integrations",
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "external_connection_requests",
        sa.Column("created_by_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "external_connection_requests",
        sa.Column("orchestration_task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "external_connection_requests",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "external_connection_requests",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_external_connection_request_message",
        "external_connection_requests",
        "channel_messages",
        ["created_by_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_external_connection_request_task",
        "external_connection_requests",
        "orchestration_tasks",
        ["orchestration_task_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_external_connection_request_task", "external_connection_requests", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_external_connection_request_message", "external_connection_requests", type_="foreignkey"
    )
    op.drop_column("external_connection_requests", "submitted_at")
    op.drop_column("external_connection_requests", "attempts")
    op.drop_column("external_connection_requests", "orchestration_task_id")
    op.drop_column("external_connection_requests", "created_by_message_id")
    op.drop_column("external_integrations", "credential_expires_at")
    op.drop_column("external_integrations", "credential_kind")
    op.drop_column("external_integrations", "credential_fingerprint")
    op.drop_column("external_integrations", "credential_ciphertext")
