"""add feedback gate and iteration decision

Revision ID: 20260718_0016
Revises: 20260718_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0016"
down_revision: str | None = "20260718_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_definition_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("metric_key", sa.String(255), nullable=False),
        sa.Column("definition_version", sa.String(128), nullable=False),
        sa.Column("definition_json", postgresql.JSONB(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "goal_id",
            "metric_key",
            "definition_version",
            name="uq_metric_bindings_goal_key_version",
        ),
    )
    op.create_table(
        "gate_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("observation_ids", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PASSED','FAILED','INSUFFICIENT_EVIDENCE')",
            name="ck_gate_evaluations_status",
        ),
    )
    op.create_table(
        "iteration_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "gate_evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gate_evaluations.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("primary_hypothesis", sa.Text()),
        sa.Column(
            "new_work_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="RESTRICT"),
        ),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "certified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('CONTINUE','REVISE','STOP')", name="ck_iteration_decisions_decision"
        ),
        sa.CheckConstraint(
            "(decision = 'REVISE' AND primary_hypothesis IS NOT NULL AND new_work_id IS NOT NULL) OR (decision <> 'REVISE' AND primary_hypothesis IS NULL AND new_work_id IS NULL)",  # noqa: E501
            name="ck_iteration_decisions_revise_fields",
        ),
    )


def downgrade() -> None:
    op.drop_table("iteration_decisions")
    op.drop_table("gate_evaluations")
    op.drop_table("metric_definition_bindings")
