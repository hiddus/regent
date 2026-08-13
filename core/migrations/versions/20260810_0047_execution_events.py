"""Add append-only durable execution events.

Revision ID: 20260810_0047
Revises: 20260810_0046
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0047"
down_revision: str | None = "20260810_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "parent_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_events.event_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "causation_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_events.event_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "work_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
        ),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "organization_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "permission_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("budget_reservation_ref", sa.String(length=255)),
        sa.Column("model_version", sa.String(length=255)),
        sa.Column(
            "tool_versions_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("event_key", name="uq_execution_events_event_key"),
        sa.UniqueConstraint(
            "goal_id", "goal_sequence", name="uq_execution_events_goal_sequence"
        ),
        sa.CheckConstraint("goal_sequence > 0", name="ck_execution_events_goal_sequence"),
    )
    op.create_index(
        "ix_execution_events_goal_replay", "execution_events", ["goal_id", "goal_sequence"]
    )
    op.create_index("ix_execution_events_run", "execution_events", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_execution_events_run", table_name="execution_events")
    op.drop_index("ix_execution_events_goal_replay", table_name="execution_events")
    op.drop_table("execution_events")
