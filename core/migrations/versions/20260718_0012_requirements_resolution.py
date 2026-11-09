"""add requirements and capability resolution

Revision ID: 20260718_0012
Revises: 20260718_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0012"
down_revision: str | None = "20260718_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirement_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "hypothesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_hypotheses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requirement_key", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "predecessor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirement_revisions.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("generator_ref", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','VALIDATED','SUPERSEDED','WITHDRAWN')",
            name="ck_requirement_revisions_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_requirement_revisions_version"),
        sa.UniqueConstraint(
            "goal_id",
            "requirement_key",
            "revision",
            name="uq_requirement_revisions_goal_key_revision",
        ),
    )
    op.create_table(
        "capability_resolution_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "requirement_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirement_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','FROZEN','SATISFIED','WAITING_HUMAN','BLOCKED','FAILED')",
            name="ck_capability_resolution_plans_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_capability_resolution_plans_version"),
        sa.UniqueConstraint(
            "requirement_revision_id", name="uq_capability_resolution_plans_requirement"
        ),
    )
    op.create_table(
        "capability_resolution_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("capability_resolution_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_key", sa.String(120), nullable=False),
        sa.Column("capability_name", sa.String(255), nullable=False),
        sa.Column("gap_type", sa.String(64), nullable=False),
        sa.Column("resolution_method", sa.String(32), nullable=False),
        sa.Column(
            "capability_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("capabilities.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "tool_spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tool_specs.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "resolution_method IN ('REUSE','CONFIGURE','COMPOSE','BUILD','REQUEST_HUMAN','BLOCK')",
            name="ck_capability_resolution_items_method",
        ),
        sa.UniqueConstraint(
            "plan_id", "requirement_key", name="uq_capability_resolution_items_requirement"
        ),
    )


def downgrade() -> None:
    op.drop_table("capability_resolution_items")
    op.drop_table("capability_resolution_plans")
    op.drop_table("requirement_revisions")
