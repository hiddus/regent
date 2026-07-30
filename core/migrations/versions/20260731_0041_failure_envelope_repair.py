"""FailureEnvelope + RepairAttempt persistence (GQ-0 / Tech-Spec §13.5).

Revision ID: 20260731_0041
Revises: 20260731_0040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0041"
down_revision: str | None = "20260731_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "failure_envelopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "generation_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workspace_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("evidence_artifact_uri", sa.Text(), nullable=True),
        sa.Column(
            "evidence_payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "policy_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="OPEN"),
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
            "stage IN ('build','test','smoke','verification','generation')",
            name="ck_failure_envelopes_stage",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','REPAIRING','CLOSED','HANDED_OFF')",
            name="ck_failure_envelopes_status",
        ),
    )
    op.create_index(
        "ix_failure_envelopes_goal_created",
        "failure_envelopes",
        ["goal_id", "created_at"],
    )
    op.create_index(
        "ix_failure_envelopes_generation_run",
        "failure_envelopes",
        ["generation_run_id"],
    )

    op.create_table(
        "repair_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "failure_envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("failure_envelopes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="REQUESTED"),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "output_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("termination_reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=False, server_default="regent-core"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('REQUESTED','RUNNING','SUCCEEDED','FAILED','EXHAUSTED','HANDED_OFF')",
            name="ck_repair_attempts_status",
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_repair_attempts_attempt_no"),
        sa.UniqueConstraint("idempotency_key", name="uq_repair_attempts_idempotency"),
        sa.UniqueConstraint(
            "failure_envelope_id",
            "attempt_no",
            name="uq_repair_attempts_envelope_no",
        ),
    )
    op.create_index(
        "ix_repair_attempts_envelope",
        "repair_attempts",
        ["failure_envelope_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_repair_attempts_envelope", table_name="repair_attempts")
    op.drop_table("repair_attempts")
    op.drop_index("ix_failure_envelopes_generation_run", table_name="failure_envelopes")
    op.drop_index("ix_failure_envelopes_goal_created", table_name="failure_envelopes")
    op.drop_table("failure_envelopes")
