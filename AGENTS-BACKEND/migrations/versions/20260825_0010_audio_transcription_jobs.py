"""Add durable Telegram audio transcription jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0010"
down_revision: str | None = "20260824_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audio_transcription_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(30), nullable=False, server_default="assemblyai"),
        sa.Column("model", sa.String(100), nullable=False, server_default="universal-2"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(30), nullable=False, server_default="upload"),
        sa.Column("telegram_file_id", sa.String(300), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("provider_upload_url", sa.Text(), nullable=True),
        sa.Column("provider_transcript_id", sa.String(200), nullable=True),
        sa.Column("provider_status", sa.String(30), nullable=True),
        sa.Column("language_code", sa.String(20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("transcript_hash", sa.String(64), nullable=True),
        sa.Column("provider_latency_ms", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(100), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail", sa.String(500), nullable=True),
        sa.Column("provider_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("channel_message_id", name="uq_audio_transcription_message"),
        sa.UniqueConstraint("provider_transcript_id", name="uq_audio_transcription_provider_id"),
    )
    op.create_index(
        "ix_audio_transcription_claim",
        "audio_transcription_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_audio_transcription_conversation",
        "audio_transcription_jobs",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audio_transcription_conversation", table_name="audio_transcription_jobs"
    )
    op.drop_index("ix_audio_transcription_claim", table_name="audio_transcription_jobs")
    op.drop_table("audio_transcription_jobs")
