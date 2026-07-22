"""add dependency resolution and offline build

Revision ID: 20260718_0014
Revises: 20260718_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0014"
down_revision: str | None = "20260718_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dependency_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("dependency_intents", postgresql.JSONB(), nullable=False),
        sa.Column("lockfile_uri", sa.String(1024)),
        sa.Column("bundle_uri", sa.String(1024)),
        sa.Column("bundle_hash", sa.String(64)),
        sa.Column("sbom_uri", sa.String(1024)),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("failure_code", sa.String(128)),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','RESOLVING','MATERIALIZED','REJECTED','FAILED','UNKNOWN')",
            name="ck_dependency_resolutions_status",
        ),
    )
    op.create_table(
        "app_builds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "dependency_resolution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dependency_resolutions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("external_operation_id", sa.String(255)),
        sa.Column("build_artifact_uri", sa.String(1024)),
        sa.Column("build_artifact_hash", sa.String(64)),
        sa.Column("log_uri", sa.String(1024)),
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
            "status IN ('QUEUED','RUNNING','PASSED','FAILED','UNKNOWN')",
            name="ck_app_builds_status",
        ),
    )
    op.create_table(
        "verification_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_build_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_builds.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_uri", sa.String(1024), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("runtime_profile_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("verification_reports")
    op.drop_table("app_builds")
    op.drop_table("dependency_resolutions")
