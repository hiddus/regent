"""Add project_agent_sessions for durable project Agent Session chassis.

Revision ID: 20260802_0043
Revises: 20260802_0042
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0043"
down_revision: str | None = "20260802_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_agent_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("workspace_uri", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_generation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_by", sa.String(length=255), nullable=False, server_default="regent-core"
        ),
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
            "status IN ('ACTIVE','PAUSED','STOPPED')",
            name="ck_project_agent_sessions_status",
        ),
        sa.CheckConstraint("epoch >= 0", name="ck_project_agent_sessions_epoch"),
        sa.CheckConstraint("version >= 0", name="ck_project_agent_sessions_version"),
    )
    op.create_index(
        "ix_project_agent_sessions_app_project_id",
        "project_agent_sessions",
        ["app_project_id"],
    )
    op.create_index(
        "ix_project_agent_sessions_goal_id",
        "project_agent_sessions",
        ["goal_id"],
    )
    op.create_index(
        "ix_project_agent_sessions_goal",
        "project_agent_sessions",
        ["goal_id", "status"],
    )
    op.create_index(
        "uq_project_agent_sessions_active_project",
        "project_agent_sessions",
        ["app_project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_project_agent_sessions_active_project",
        table_name="project_agent_sessions",
    )
    op.drop_index("ix_project_agent_sessions_goal", table_name="project_agent_sessions")
    op.drop_index("ix_project_agent_sessions_goal_id", table_name="project_agent_sessions")
    op.drop_index(
        "ix_project_agent_sessions_app_project_id",
        table_name="project_agent_sessions",
    )
    op.drop_table("project_agent_sessions")
