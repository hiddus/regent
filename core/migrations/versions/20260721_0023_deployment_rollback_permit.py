"""add deployments.rollback_permit_id

Revision ID: 20260721_0023
Revises: 20260720_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0023"
down_revision: str | None = "20260720_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column(
            "rollback_permit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_permits.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("deployments", "rollback_permit_id")
