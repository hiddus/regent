"""Harness² 改进库表（兼作本书覆盖层）。

Revision ID: 20261020_0057
Revises: 20260913_0056
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261020_0057"
down_revision: str | None = "20260913_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "novel_harness_library",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
        sa.Column("scope_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("genre_tag", sa.String(64), nullable=False, server_default=""),
        sa.Column("chapter_role", sa.String(64), nullable=False, server_default=""),
        sa.Column("failure_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("config_tree", JSONB, nullable=False, server_default="{}"),
        sa.Column("config_diff", JSONB, nullable=False, server_default="{}"),
        sa.Column("experience", sa.Text, nullable=False, server_default=""),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default="[]"),
        sa.Column("adopted_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("effective_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("rollback_reason", sa.Text, nullable=False, server_default=""),
        sa.Column("parent_entry_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_work_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("harness_hash", sa.String(32), nullable=False, server_default=""),
        sa.Column("overlay_after", JSONB, nullable=True),
        sa.Column("since_chapter", sa.Integer, nullable=True),
        sa.Column("baseline", JSONB, nullable=False, server_default="{}"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_harness_lib_scope_type", "novel_harness_library", ["scope", "genre_tag"])
    op.create_index("ix_harness_lib_failure", "novel_harness_library", ["failure_code"])
    op.create_index(
        "ix_harness_lib_work_status", "novel_harness_library", ["source_work_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_harness_lib_work_status", table_name="novel_harness_library")
    op.drop_index("ix_harness_lib_failure", table_name="novel_harness_library")
    op.drop_index("ix_harness_lib_scope_type", table_name="novel_harness_library")
    op.drop_table("novel_harness_library")
