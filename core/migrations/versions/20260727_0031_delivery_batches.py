"""Add delivery_batches table for incremental generate-verify-merge.

Revision ID: 20260727_0031
Revises: 20260727_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0031"
down_revision: str | None = "20260727_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "app_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("milestone_key", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("milestone_ordinal", sa.Integer(), nullable=True),
        sa.Column("batch_ordinal", sa.Integer(), nullable=False),
        sa.Column("batch_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PLANNED"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope_paths", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("acceptance_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "base_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "result_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("workspace_locator", sa.String(length=1024), nullable=True),
        sa.Column("verification_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("correlation_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
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
            "status IN ('PLANNED','GENERATING','VERIFYING','MERGED','REJECTED','CANCELLED')",
            name="ck_delivery_batches_status",
        ),
        sa.CheckConstraint("batch_ordinal >= 1", name="ck_delivery_batches_ordinal"),
        sa.CheckConstraint("attempt >= 1", name="ck_delivery_batches_attempt"),
        sa.CheckConstraint("version >= 0", name="ck_delivery_batches_version"),
        sa.UniqueConstraint(
            "generation_run_id",
            "batch_key",
            name="uq_delivery_batches_run_key",
        ),
    )
    op.create_index("ix_delivery_batches_goal_id", "delivery_batches", ["goal_id"])
    op.create_index("ix_delivery_batches_app_project_id", "delivery_batches", ["app_project_id"])
    op.create_index(
        "ix_delivery_batches_generation_run_id", "delivery_batches", ["generation_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_batches_generation_run_id", table_name="delivery_batches")
    op.drop_index("ix_delivery_batches_app_project_id", table_name="delivery_batches")
    op.drop_index("ix_delivery_batches_goal_id", table_name="delivery_batches")
    op.drop_table("delivery_batches")
