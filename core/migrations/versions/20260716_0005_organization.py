"""capability and organization models

Revision ID: 20260716_0005
Revises: 20260716_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0005"
down_revision: str | None = "20260716_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "scope_goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("verification", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','GOAL_CERTIFIED','VERIFIED','REVOKED')",
            name="ck_capabilities_status",
        ),
        sa.UniqueConstraint("name", "scope_goal_id", name="uq_capabilities_name_scope"),
    )
    op.create_table(
        "agent_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "scope_goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
        ),
        sa.Column("capability_names", postgresql.JSONB(), nullable=False),
        sa.Column("model_ref", sa.String(255), nullable=False),
        sa.Column("tool_refs", postgresql.JSONB(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','ACTIVE','REVOKED')", name="ck_agent_specs_status"
        ),
        sa.UniqueConstraint(
            "name", "version", "scope_goal_id", name="uq_agent_specs_name_version_scope"
        ),
    )
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("max_agents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('ACTIVE','DISSOLVED')", name="ck_organizations_status"),
    )
    op.create_table(
        "assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "agent_spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_specs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(128), nullable=False),
        sa.Column("delegated_capabilities", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "work_id", name="uq_assignments_org_work"),
    )


def downgrade() -> None:
    op.drop_table("assignments")
    op.drop_table("organizations")
    op.drop_table("agent_specs")
    op.drop_table("capabilities")
