"""Add works.sub_goal_id column (P1-B SubGoal link)

Revision ID: 20260725_0028
Revises: 20260725_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0028"
down_revision: str | None = "20260725_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "works",
        sa.Column("sub_goal_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_works_sub_goal_id",
        "works",
        ["sub_goal_id"],
    )
    op.add_column(
        "works",
        sa.Column("depends_on_work_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("works", "depends_on_work_ids")
    op.drop_index("ix_works_sub_goal_id", table_name="works")
    op.drop_column("works", "sub_goal_id")
