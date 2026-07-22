"""add auditable conversation commands

Revision ID: 20260718_0019
Revises: 20260718_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0019"
down_revision: str | None = "20260718_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("command_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("interpretation_json", postgresql.JSONB(), nullable=False),
        sa.Column("interpretation_hash", sa.String(64), nullable=False),
        sa.Column(
            "resulting_goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="SET NULL"),
        ),
        sa.Column("model_ref", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "command_type IN ('QUERY','MODIFY','CONTINUE')",
            name="ck_conversation_commands_type",
        ),
        sa.CheckConstraint(
            "status IN ('INTERPRETED','APPLIED','FAILED')",
            name="ck_conversation_commands_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("conversation_commands")
