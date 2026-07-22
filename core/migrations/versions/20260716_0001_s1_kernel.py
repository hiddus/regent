"""Create the S1 reliable kernel tables.

Revision ID: 20260716_0001
Revises:
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_input", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','READY','ACTIVE','PAUSED','WAITING_HUMAN',"
            "'BLOCKED','ACHIEVED','EXHAUSTED','FAILED','CANCELLED')",
            name="ck_goals_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_goals_correlation_id", "goals", ["correlation_id"])

    op.create_table(
        "goal_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("explicit_constraints", postgresql.JSONB(), nullable=False),
        sa.Column("system_inferences", postgresql.JSONB(), nullable=False),
        sa.Column("unknowns", postgresql.JSONB(), nullable=False),
        sa.Column("success_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name="ck_goal_specs_positive_version"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("goal_id", "version", name="uq_goal_specs_goal_version"),
    )
    op.create_index("ix_goal_specs_goal_id", "goal_specs", ["goal_id"])

    op.create_table(
        "works",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("input_refs", postgresql.JSONB(), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("dependency_ids", postgresql.JSONB(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED','READY','RUNNING','EVALUATING','ACCEPTED',"
            "'REJECTED','WAITING_HUMAN','BLOCKED','UNKNOWN','CANCELLED')",
            name="ck_works_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_works_nonnegative_version"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_works_correlation_id", "works", ["correlation_id"])
    op.create_index("ix_works_goal_id", "works", ["goal_id"])

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(length=255)),
        sa.Column("agent_spec_ref", sa.String(length=255)),
        sa.Column("model_ref", sa.String(length=255)),
        sa.Column("tool_ref", sa.String(length=255)),
        sa.Column("input_version", sa.String(length=255), nullable=False),
        sa.Column("permit_id", postgresql.UUID(as_uuid=True)),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("resource_usage", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('CREATED','PERMIT_PENDING','QUEUED','RUNNING','EXECUTED',"
            "'FAILED','UNKNOWN','DENIED','EXPIRED','CANCELLED')",
            name="ck_runs_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_runs_nonnegative_version"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_runs_correlation_id", "runs", ["correlation_id"])
    op.create_index("ix_runs_work_id", "runs", ["work_id"])
    op.create_index(
        "uq_runs_one_active_per_work",
        "runs",
        ["work_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CREATED','PERMIT_PENDING','QUEUED','RUNNING')"),
    )

    op.create_table(
        "audit_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_aggregate_timeline",
        "audit_records",
        ["aggregate_type", "aggregate_id", "occurred_at"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True)),
        sa.CheckConstraint(
            "status IN ('PENDING','DISPATCHING','DISPATCHED','FAILED')",
            name="ck_outbox_events_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_pending", "outbox_events", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_pending", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_audit_aggregate_timeline", table_name="audit_records")
    op.drop_table("audit_records")
    op.drop_index("uq_runs_one_active_per_work", table_name="runs")
    op.drop_index("ix_runs_work_id", table_name="runs")
    op.drop_index("ix_runs_correlation_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_works_goal_id", table_name="works")
    op.drop_index("ix_works_correlation_id", table_name="works")
    op.drop_table("works")
    op.drop_index("ix_goal_specs_goal_id", table_name="goal_specs")
    op.drop_table("goal_specs")
    op.drop_index("ix_goals_correlation_id", table_name="goals")
    op.drop_table("goals")
