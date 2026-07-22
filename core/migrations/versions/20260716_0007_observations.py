"""observations and experience records

Revision ID: 20260716_0007
Revises: 20260716_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0007"
down_revision: str | None = "20260716_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "goal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goals.id", ondelete="SET NULL")
        ),
        sa.Column("metric_name", sa.String(255), nullable=False),
        sa.Column("metric_value", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("definition_version", sa.String(128), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("is_bot", sa.Boolean(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "experience_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observation_ids", postgresql.JSONB(), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("replan_triggered", sa.Boolean(), nullable=False),
        sa.Column("attribution", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("experience_records")
    op.drop_table("observations")
