"""tool specs and certifications

Revision ID: 20260716_0006
Revises: 20260716_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0006"
down_revision: str | None = "20260716_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("capability_name", sa.String(255), nullable=False),
        sa.Column(
            "scope_goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entrypoint", sa.String(512), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','CERTIFIED','REVOKED')", name="ck_tool_specs_status"
        ),
        sa.UniqueConstraint(
            "name", "version", "scope_goal_id", name="uq_tool_specs_name_version_scope"
        ),
    )
    op.create_table(
        "tool_certifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tool_spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tool_specs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_passed", sa.Boolean(), nullable=False),
        sa.Column("hidden_passed", sa.Boolean(), nullable=False),
        sa.Column("public_evidence_hash", sa.String(64), nullable=False),
        sa.Column("hidden_evidence_hash", sa.String(64), nullable=False),
        sa.Column("security_checks", postgresql.JSONB(), nullable=False),
        sa.Column(
            "certified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("tool_certifications")
    op.drop_table("tool_specs")
