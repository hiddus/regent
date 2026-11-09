"""add preview release control plane

Revision ID: 20260718_0015
Revises: 20260718_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0015"
down_revision: str | None = "20260718_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_build_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_builds.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "human_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("human_tasks.id", ondelete="RESTRICT"),
        ),
        sa.Column("approved_by", sa.String(255)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','READY','APPROVED','REJECTED')", name="ck_release_candidates_status"
        ),
    )
    op.create_table(
        "deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "release_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "permit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_permits.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("external_deployment_id", sa.String(255), unique=True),
        sa.Column("endpoint", sa.String(1024)),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("failure_code", sa.String(128)),
        sa.Column(
            "reconciliation_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','DEPLOYING','SUCCEEDED','FAILED','UNKNOWN','SUPERSEDED','ROLLED_BACK')",  # noqa: E501
            name="ck_deployments_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("deployments")
    op.drop_table("release_candidates")
