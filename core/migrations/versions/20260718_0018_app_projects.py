"""add app projects and goal confirmation gate

Revision ID: 20260718_0018
Revises: 20260718_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0018"
down_revision: str | None = "20260718_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("product_intent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','STOPPED','ARCHIVED')",
            name="ck_app_projects_status",
        ),
    )
    op.add_column(
        "goals", sa.Column("app_project_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_goals_app_project",
        "goals",
        "app_projects",
        ["app_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_goals_app_project_id", "goals", ["app_project_id"])
    op.add_column(
        "conversations",
        sa.Column("app_project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_app_project",
        "conversations",
        "app_projects",
        ["app_project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_conversations_app_project", "conversations", ["app_project_id"])
    op.add_column(
        "goal_specs",
        sa.Column("status", sa.String(16), nullable=False, server_default="FROZEN"),
    )
    op.add_column(
        "goal_specs",
        sa.Column(
            "content_hash", sa.String(64), nullable=False, server_default="legacy-unverified"
        ),
    )
    op.add_column("goal_specs", sa.Column("confirmed_by", sa.String(255), nullable=True))
    op.add_column(
        "goal_specs", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_goal_specs_status", "goal_specs", "status IN ('DRAFT','FROZEN','SUPERSEDED')"
    )
    op.alter_column("goal_specs", "status", server_default=None)
    op.alter_column("goal_specs", "content_hash", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_goal_specs_status", "goal_specs", type_="check")
    op.drop_column("goal_specs", "confirmed_at")
    op.drop_column("goal_specs", "confirmed_by")
    op.drop_column("goal_specs", "content_hash")
    op.drop_column("goal_specs", "status")
    op.drop_constraint("uq_conversations_app_project", "conversations", type_="unique")
    op.drop_constraint("fk_conversations_app_project", "conversations", type_="foreignkey")
    op.drop_column("conversations", "app_project_id")
    op.drop_index("ix_goals_app_project_id", table_name="goals")
    op.drop_constraint("fk_goals_app_project", "goals", type_="foreignkey")
    op.drop_column("goals", "app_project_id")
    op.drop_table("app_projects")
