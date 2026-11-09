"""add supervised self improvement candidates

Revision ID: 20260718_0021
Revises: 20260718_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0021"
down_revision: str | None = "20260718_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "self_improvement_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("primary_problem", sa.Text(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("target_file", sa.String(1024), nullable=False),
        sa.Column("baseline_hash", sa.String(64), nullable=False),
        sa.Column("candidate_hash", sa.String(64)),
        sa.Column("candidate_workspace", sa.String(1024)),
        sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("verification_json", postgresql.JSONB(), nullable=False),
        sa.Column("risk_json", postgresql.JSONB(), nullable=False),
        sa.Column("model_ref", sa.String(255)),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("failure_code", sa.String(128)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("approved_by", sa.String(255)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED','CANDIDATE_READY','AWAITING_APPROVAL',"
            "'APPROVED','REJECTED','FAILED')",
            name="ck_self_improvement_runs_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("self_improvement_runs")
