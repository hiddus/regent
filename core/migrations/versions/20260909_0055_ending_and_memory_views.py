"""终局意图、记忆边类型与六类记忆视图（Plan §4 R2/R3）。

Revision ID: 20260909_0055
Revises: 20260908_0054

三件事，一起上是因为它们同属「记忆与终局必须是被记录的事实，不是生成副产物」：

- ``novel_works`` 记用户认可的终局（目标卷数 / 结局描述）。没有它，末节点完成
  后只能「先扩卷，扩不出来才结束」——正常故事永远不结束（B-05）。
- ``novel_memory_edges.edge_kind`` 区分「这条边是真实依赖」与「这条边是显式确认
  没有依赖」。只有前者时，孤立节点既可能是真的独立，也可能是漏记了依赖，
  两种情况的处置完全不同（B-02）。
- ``novel_memory_items`` 的 kind 扩展到技术方案 §3.2 的六个视图，并加
  ``resolved_chapter_no``：承诺在哪一章被兑现必须能查，否则「已兑现」和
  「从没被记住」在存储里长得一样（B-01）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0055"
down_revision: str | None = "20260908_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_KINDS = (
    "rule",
    "character_arc",
    "promise",
    "relation",
    "belief",
    "reader_knowledge",
    "director_note",
)
_KINDS_SQL = ", ".join(f"'{kind}'" for kind in _NEW_KINDS)
_OLD_KINDS_SQL = "'rule', 'character_arc', 'promise', 'relation'"


def upgrade() -> None:
    op.add_column(
        "novel_works",
        sa.Column("ending_target_volume", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "novel_works",
        sa.Column("ending_statement", sa.String(500), nullable=False, server_default=""),
    )
    op.add_column(
        "novel_memory_edges",
        sa.Column("edge_kind", sa.String(16), nullable=False, server_default="depends"),
    )
    op.add_column(
        "novel_memory_items",
        sa.Column("resolved_chapter_no", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "novel_memory_items",
        sa.Column("basis", sa.String(32), nullable=False, server_default=""),
    )
    op.drop_constraint("ck_novel_memory_kind", "novel_memory_items", type_="check")
    op.create_check_constraint(
        "ck_novel_memory_kind", "novel_memory_items", f"kind IN ({_KINDS_SQL})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_novel_memory_kind", "novel_memory_items", type_="check")
    op.create_check_constraint(
        "ck_novel_memory_kind", "novel_memory_items", f"kind IN ({_OLD_KINDS_SQL})"
    )
    op.drop_column("novel_memory_items", "basis")
    op.drop_column("novel_memory_items", "resolved_chapter_no")
    op.drop_column("novel_memory_edges", "edge_kind")
    op.drop_column("novel_works", "ending_statement")
    op.drop_column("novel_works", "ending_target_volume")
