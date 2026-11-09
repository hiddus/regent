"""P2 committed packages: runtime profiles, eval runs, memory, preempt, checkpoints

Revision ID: 20260723_0026
Revises: 20260723_0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0026"
down_revision: str | None = "20260723_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "abi_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sandbox_image", sa.String(255)),
        sa.Column("resolver_image", sa.String(255)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','CERTIFIED','DEPRECATED','REVOKED')",
            name="ck_runtime_profiles_status",
        ),
        sa.UniqueConstraint("name", "version", name="uq_runtime_profiles_name_version"),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("task_set_json", postgresql.JSONB(), nullable=False),
        sa.Column("task_set_hash", sa.String(64), nullable=False),
        sa.Column(
            "baseline_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "budget_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("seed", sa.String(64), nullable=False),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "scores_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("decision", sa.String(64)),
        sa.Column("decision_rationale", sa.Text()),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','FROZEN','RUNNING','SCORED','DECIDED','INVALIDATED')",
            name="ck_eval_runs_status",
        ),
    )
    op.create_index("ix_eval_runs_status", "eval_runs", ["status"])

    op.create_table(
        "memory_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("content_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "source_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','VERIFIED','SUPERSEDED','REVOKED','EXPIRED')",
            name="ck_memory_records_status",
        ),
    )
    op.create_index("ix_memory_records_org_status", "memory_records", ["org_key", "status"])

    op.create_table(
        "preemption_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "queue_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_queue_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reservation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resource_reservations.id", ondelete="SET NULL"),
        ),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("safe", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "scheduler_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column(
            "scheduling_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduling_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("scheduler_checkpoints")
    op.drop_table("preemption_records")
    op.drop_table("memory_records")
    op.drop_table("eval_runs")
    op.drop_table("runtime_profiles")
