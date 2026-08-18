"""Persist the fast agent routing context for orchestration handoffs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0004"
down_revision: str | None = "20260818_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orchestration_tasks",
        sa.Column(
            "routing_context",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.alter_column("orchestration_tasks", "routing_context", server_default=None)


def downgrade() -> None:
    op.drop_column("orchestration_tasks", "routing_context")
