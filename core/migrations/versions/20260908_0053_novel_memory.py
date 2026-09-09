"""Novel Engine 长期创作记忆（Plan §4 R3）。

Revision ID: 20260908_0053
Revises: 20260908_0052

覆盖：
- novel_memory_items：稳定规则 / 人物弧线 / 承诺与伏笔 / 关系变化的可召回索引
- novel_memory_edges：依赖边，用于最小子图重演

事实链（novel_canon_commits）保持 append-only，本迁移只加**索引表**：改意时
条目置 invalidated_at 而非删除，旧版上下文不会污染新版。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260908_0053"
down_revision: str | None = "20260908_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = "('rule','character_arc','promise','relation')"
_STATES = "('OPEN','RESOLVED','ABANDONED')"


def upgrade() -> None:
    op.create_table(
        "novel_memory_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("branch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("item_key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False, server_default=""),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("entities", JSONB, nullable=False, server_default="[]"),
        sa.Column("state", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="high"),
        sa.Column("source_chapter_no", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("memory_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("work_id", "branch_id", "item_key", name="uq_novel_memory_key"),
        sa.CheckConstraint(f"kind IN {_KINDS}", name="ck_novel_memory_kind"),
        sa.CheckConstraint(f"state IN {_STATES}", name="ck_novel_memory_state"),
    )
    op.create_index(
        "ix_novel_memory_work_state", "novel_memory_items", ["work_id", "state"]
    )

    op.create_table(
        "novel_memory_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("branch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("upstream_key", sa.String(255), nullable=False),
        sa.Column("downstream_key", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "work_id", "branch_id", "upstream_key", "downstream_key",
            name="uq_novel_memory_edge",
        ),
    )
    op.create_index(
        "ix_novel_memory_edge_up", "novel_memory_edges", ["work_id", "upstream_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_novel_memory_edge_up", table_name="novel_memory_edges")
    op.drop_table("novel_memory_edges")
    op.drop_index("ix_novel_memory_work_state", table_name="novel_memory_items")
    op.drop_table("novel_memory_items")
