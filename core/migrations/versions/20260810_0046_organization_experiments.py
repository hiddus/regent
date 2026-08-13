"""Add sandbox organization experiments.

Revision ID: 20260810_0046
Revises: 20260810_0045
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0046"
down_revision: str | None = "20260810_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "base_organization_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_topology_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("mutations_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resource_lease_ref", sa.String(length=255), nullable=False),
        sa.Column(
            "execution_mode", sa.String(length=16), nullable=False, server_default="SANDBOX"
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="SHADOW"),
        sa.Column(
            "evaluation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "candidate_organization_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "rollback_organization_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(length=255), nullable=False),
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
            "status IN ('SHADOW','EVALUATED','ADOPTED','REJECTED')",
            name="ck_organization_experiments_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_organization_experiments_version"),
        sa.CheckConstraint(
            "execution_mode = 'SANDBOX'",
            name="ck_organization_experiments_sandbox_only",
        ),
        sa.UniqueConstraint(
            "organization_id", "version", name="uq_organization_experiments_org_version"
        ),
    )
    op.create_index(
        "ix_organization_experiments_goal_status",
        "organization_experiments",
        ["goal_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_experiments_goal_status",
        table_name="organization_experiments",
    )
    op.drop_table("organization_experiments")
