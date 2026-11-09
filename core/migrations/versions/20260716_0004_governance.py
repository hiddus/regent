"""execution permits and human tasks

Revision ID: 20260716_0004
Revises: 20260716_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0004"
down_revision: str | None = "20260716_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_permits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("parameter_hash", sa.String(64), nullable=False),
        sa.Column("data_scope", postgresql.JSONB(), nullable=False),
        sa.Column("network_scope", postgresql.JSONB(), nullable=False),
        sa.Column("resource_limit", postgresql.JSONB(), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("nonce", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','APPROVED','CLAIMED','CONSUMED','DENIED','EXPIRED','REVOKED')",
            name="ck_execution_permits_status",
        ),
    )
    op.create_index(
        "ix_execution_permits_valid_until", "execution_permits", ["status", "valid_until"]
    )
    op.create_table(
        "human_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="RESTRICT")
        ),
        sa.Column("task_type", sa.String(128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("assigned_to", sa.String(255)),
        sa.Column("response", postgresql.JSONB()),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','COMPLETED','TIMED_OUT','CANCELLED')", name="ck_human_tasks_status"
        ),
    )
    op.create_index("ix_human_tasks_due", "human_tasks", ["status", "due_at"])


def downgrade() -> None:
    op.drop_table("human_tasks")
    op.drop_table("execution_permits")
