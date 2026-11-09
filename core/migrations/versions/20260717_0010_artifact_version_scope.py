"""scope artifact versions to their logical owner

Revision ID: 20260717_0010
Revises: 20260716_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0010"
down_revision: str | None = "20260716_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_artifacts_goal_type_version", "artifacts", type_="unique")
    op.create_index(
        "uq_artifacts_work_type_version",
        "artifacts",
        ["work_id", "artifact_type", "version"],
        unique=True,
        postgresql_where=sa.text("work_id IS NOT NULL"),
    )
    op.create_index(
        "uq_artifacts_goal_type_version_unscoped",
        "artifacts",
        ["goal_id", "artifact_type", "version"],
        unique=True,
        postgresql_where=sa.text("work_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_artifacts_goal_type_version_unscoped", table_name="artifacts")
    op.drop_index("uq_artifacts_work_type_version", table_name="artifacts")
    op.create_unique_constraint(
        "uq_artifacts_goal_type_version",
        "artifacts",
        ["goal_id", "artifact_type", "version"],
    )
