"""Add immutable artifacts, evidence, and durable timers.

Revision ID: 20260716_0003
Revises: 20260716_0002
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True)),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("artifact_type", sa.String(length=128), nullable=False),
        sa.Column("schema_ref", sa.String(length=512)),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("producer_ref", sa.String(length=255), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name="ck_artifacts_positive_version"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "goal_id", "artifact_type", "version", name="uq_artifacts_goal_type_version"
        ),
    )
    op.create_index("ix_artifacts_goal_id", "artifacts", ["goal_id"])
    op.create_index("ix_artifacts_work_id", "artifacts", ["work_id"])
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_content_hash", "artifacts", ["content_hash"])

    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True)),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("evidence_type", sa.String(length=128), nullable=False),
        sa.Column("uri", sa.Text()),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("producer_ref", sa.String(length=255), nullable=False),
        sa.Column("quality_tier", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_goal_id", "evidence", ["goal_id"])
    op.create_index("ix_evidence_work_id", "evidence", ["work_id"])
    op.create_index("ix_evidence_run_id", "evidence", ["run_id"])

    op.create_table(
        "durable_timers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','CLAIMED','FIRED','CANCELLED','FAILED')",
            name="ck_durable_timers_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_durable_timers_due", "durable_timers", ["status", "due_at", "lease_expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_durable_timers_due", table_name="durable_timers")
    op.drop_table("durable_timers")
    op.drop_index("ix_evidence_run_id", table_name="evidence")
    op.drop_index("ix_evidence_work_id", table_name="evidence")
    op.drop_index("ix_evidence_goal_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_artifacts_content_hash", table_name="artifacts")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_index("ix_artifacts_work_id", table_name="artifacts")
    op.drop_index("ix_artifacts_goal_id", table_name="artifacts")
    op.drop_table("artifacts")
