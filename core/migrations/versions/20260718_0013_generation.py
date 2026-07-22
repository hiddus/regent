"""add generation control plane

Revision ID: 20260718_0013
Revises: 20260718_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0013"
down_revision: str | None = "20260718_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "requirement_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirement_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "capability_resolution_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("capability_resolution_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("contract_json", postgresql.JSONB(), nullable=False),
        sa.Column("architecture_summary", sa.Text(), nullable=False),
        sa.Column("component_plan", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','FROZEN','EXECUTING','COMPLETED','FAILED','CANCELLED')",
            name="ck_generation_plans_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_generation_plans_version"),
    )
    op.create_table(
        "generation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("model_ref", sa.String(255)),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("change_set_digest", sa.String(64)),
        sa.Column("failure_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','PLANNING','GENERATING','VALIDATING','COMMITTING','COMPLETED','FAILED','CANCELLED')",  # noqa: E501
            name="ck_generation_runs_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_generation_runs_version"),
        sa.UniqueConstraint("plan_id", "attempt", name="uq_generation_runs_plan_attempt"),
    )
    op.create_table(
        "file_change_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("content_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("generator_ref", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "workspace_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("manifest_uri", sa.String(1024), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("source_archive_uri", sa.String(1024), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("workspace_locator", sa.String(1024), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("runtime_profile_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("workspace_snapshots")
    op.drop_table("file_change_sets")
    op.drop_table("generation_runs")
    op.drop_table("generation_plans")
