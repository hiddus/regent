"""frozen experiments and product decisions

Revision ID: 20260716_0009
Revises: 20260716_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0009"
down_revision: str | None = "20260716_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("name", "version", name="uq_experiment_manifests_name_version"),
    )
    op.create_table(
        "experiment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "manifest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_manifests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("task_class", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(1), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("agent_count", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_cost", sa.Float(), nullable=False),
        sa.Column("human_minutes", sa.Float(), nullable=False),
        sa.Column("human_task_count", sa.Integer(), nullable=False),
        sa.Column("safety_incidents", sa.Integer(), nullable=False),
        sa.Column("coordination_tokens", sa.Integer(), nullable=False),
        sa.Column("predicted_gap", sa.String(64), nullable=False),
        sa.Column("true_gap", sa.String(64), nullable=False),
        sa.Column("capability_reused", sa.Boolean(), nullable=False),
        sa.Column("recovery_correct", sa.Boolean(), nullable=False),
        sa.Column("failure_class", sa.String(128)),
        sa.Column("raw_evidence_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "manifest_id", "task_id", "mode", "repetition", name="uq_experiment_runs_cell"
        ),
        sa.CheckConstraint("mode IN ('A','B','C')", name="ck_experiment_runs_mode"),
    )
    op.create_table(
        "product_decision_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "manifest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_manifests.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("product_decision_records")
    op.drop_table("experiment_runs")
    op.drop_table("experiment_manifests")
