"""Stage schedule updates until the user confirms them."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_0013"
down_revision: str | None = "20260903_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduled_automations",
        sa.Column("pending_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "scheduled_automations",
        sa.Column("pending_spec", sa.JSON(), nullable=True),
    )
    op.add_column(
        "scheduled_automations",
        sa.Column("pending_original_request", sa.Text(), nullable=True),
    )
    op.add_column(
        "scheduled_automations",
        sa.Column("pending_next_run_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_automations", "pending_next_run_at")
    op.drop_column("scheduled_automations", "pending_original_request")
    op.drop_column("scheduled_automations", "pending_spec")
    op.drop_column("scheduled_automations", "pending_revision")
