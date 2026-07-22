"""add product discovery records

Revision ID: 20260718_0011
Revises: 20260717_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0011"
down_revision: str | None = "20260717_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_rounds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("failure_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','RESEARCHING','READY','DECIDED',"
            "'BLOCKED','FAILED','EXHAUSTED')",
            name="ck_discovery_rounds_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_discovery_rounds_version"),
        sa.UniqueConstraint("goal_id", "round", name="uq_discovery_rounds_goal_round"),
    )
    op.create_index("ix_discovery_rounds_correlation_id", "discovery_rounds", ["correlation_id"])
    op.create_table(
        "product_hypotheses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "round_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_key", sa.String(80), nullable=False),
        sa.Column("content_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("eligibility", sa.String(16), nullable=False),
        sa.Column("invalid_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("generator_ref", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "eligibility IN ('ELIGIBLE','INVALID')", name="ck_product_hypotheses_eligibility"
        ),
        sa.UniqueConstraint(
            "round_id", "candidate_key", name="uq_product_hypotheses_round_candidate"
        ),
    )
    op.create_table(
        "hypothesis_evidence_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hypothesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_hypotheses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("claim_key", sa.String(120), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "hypothesis_id", "evidence_id", "claim_key", name="uq_hypothesis_evidence_claim"
        ),
    )
    op.create_table(
        "hypothesis_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "round_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_rounds.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column(
            "selected_hypothesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_hypotheses.id", ondelete="RESTRICT"),
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "decision IN ('SELECT','RESEARCH_MORE','STOP')", name="ck_hypothesis_decisions_decision"
        ),
        sa.CheckConstraint(
            "(decision = 'SELECT' AND selected_hypothesis_id IS NOT NULL) OR "
            "(decision <> 'SELECT' AND selected_hypothesis_id IS NULL)",
            name="ck_hypothesis_decisions_selection",
        ),
    )


def downgrade() -> None:
    op.drop_table("hypothesis_decisions")
    op.drop_table("hypothesis_evidence_refs")
    op.drop_table("product_hypotheses")
    op.drop_index("ix_discovery_rounds_correlation_id", table_name="discovery_rounds")
    op.drop_table("discovery_rounds")
