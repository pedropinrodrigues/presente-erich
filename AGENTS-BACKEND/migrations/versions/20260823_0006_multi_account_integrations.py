"""Support multiple named external accounts per toolkit."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0006"
down_revision: str | None = "20260819_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "external_integrations",
        sa.Column("account_label", sa.String(100), nullable=True),
    )
    op.add_column(
        "external_integrations",
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY workspace_id, user_id, toolkit_slug
                       ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                                updated_at DESC,
                                created_at DESC
                   ) AS position
            FROM external_integrations
        )
        UPDATE external_integrations AS integration
        SET is_default = true
        FROM ranked
        WHERE integration.id = ranked.id AND ranked.position = 1
        """
    )
    op.alter_column("external_integrations", "is_default", server_default=None)
    op.create_index(
        "ix_external_integrations_default",
        "external_integrations",
        ["workspace_id", "user_id", "toolkit_slug", "is_default"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_integrations_default", table_name="external_integrations")
    op.drop_column("external_integrations", "is_default")
    op.drop_column("external_integrations", "account_label")
