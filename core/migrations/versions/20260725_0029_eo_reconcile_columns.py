"""Add external_operations reconcile columns (P0-A)

Revision ID: 20260725_0029
Revises: 20260725_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0029"
down_revision: str | None = "20260725_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "external_operations",
        sa.Column(
            "reconcile_attempts",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "external_operations",
        sa.Column(
            "reconcile_deadline",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("external_operations", "reconcile_deadline")
    op.drop_column("external_operations", "reconcile_attempts")
