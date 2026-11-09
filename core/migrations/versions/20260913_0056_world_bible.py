"""世界书字段：开写前编剧草稿与作品锁定副本。

Revision ID: 20260913_0056
Revises: 20260909_0055
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260913_0056"
down_revision: str | None = "20260909_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "novel_onboarding_sessions",
        sa.Column("world_bible", JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "novel_onboarding_sessions",
        sa.Column("world_bible_locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "novel_works",
        sa.Column("story_bible", JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "novel_works",
        sa.Column("story_bible_locked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("novel_works", "story_bible_locked_at")
    op.drop_column("novel_works", "story_bible")
    op.drop_column("novel_onboarding_sessions", "world_bible_locked_at")
    op.drop_column("novel_onboarding_sessions", "world_bible")
