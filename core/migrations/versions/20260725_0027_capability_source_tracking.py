"""Capability source tracking for ACQUIRE resolution

Revision ID: 20260725_0027
Revises: 20260723_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0027"
down_revision: str | None = "20260723_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "capabilities",
        sa.Column("source_url", sa.String(2048), nullable=True),
    )
    op.add_column(
        "capabilities",
        sa.Column("source_hash", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("capabilities", "source_hash")
    op.drop_column("capabilities", "source_url")
