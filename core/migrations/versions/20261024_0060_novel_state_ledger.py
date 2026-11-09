"""状态账本四张表：实体册、当前态、变更流水、处理记录。

为什么是四张：

1. `novel_canon_commits` 存的是**陈述**（这一章发生过什么），回答不了「那枚铜钥现在在谁手上」——
   两条互相覆盖的陈述可以同时成立。要能回答，必须有一行**当前值**，于是 `slots`。
2. 「这个名字算不算本书的实体」与「它现在什么状态」不是同一个问题。把章纲的物件声明
   塞进 `slots` 只能造出 `value` 为空的槽位，而空 `value` 与「没有这一行」在比对时
   无法区分，两者都被读成「我不知道」。所以登记的产物单独放 `entities`。
3. `changes` 是 append-only 的变更流水，上面有四个状态，不能混：
   `verified`（值与引文**机械绑定**，可投影）、`candidate`（引文真实但值未被机械支持，
   只留证）、`conflict`（本章同槽位出现两个都站得住的值，先后不可判，隔离）、
   `superseded`（同章被新稿替换）。
4. `runs` 是**处理完成记录**。没有它，「这一章处理过、结果是空」与「还没处理」无法区分，
   空结果会被反复重算，而重算会重复计费。

Revision ID: 20261024_0060
Revises: 20261023_0059
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261024_0060"
down_revision: str | None = "20261023_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLOTS = "('holder','lifecycle','location','present','clock','count')"
_SLOT_ORIGINS = "('plan','settled','prose')"
_CHANGE_STATUS = "('verified','candidate','conflict','superseded')"
_CHANGE_ORIGINS = "('plan','settled','prose','corrected')"
_RUN_STATUS = "('ok','empty','failed','budget','unknown')"


def upgrade() -> None:
    op.create_table(
        "novel_state_entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("branch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("entity", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="object"),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("first_chapter", sa.Integer, nullable=False),
        sa.Column("source_quote", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("work_id", "branch_id", "entity", name="uq_novel_state_entity"),
        sa.CheckConstraint("kind IN ('object','place','person')", name="ck_novel_state_entity_kind"),
        sa.CheckConstraint(
            "origin IN ('plan','settled','manual')", name="ck_novel_state_entity_origin"
        ),
    )
    op.create_index(
        "ix_novel_state_entity_work", "novel_state_entities", ["work_id", "branch_id"]
    )

    op.create_table(
        "novel_state_slots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("branch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("entity", sa.String(120), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("as_of_chapter", sa.Integer, nullable=False),
        sa.Column("source_quote", sa.Text, nullable=False, server_default=""),
        sa.Column("origin", sa.String(16), nullable=False),
        # 这一行由哪条流水投影出来：没有来源的当前值不可审计。
        sa.Column("source_change_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("work_id", "branch_id", "entity", "slot", name="uq_novel_state_slot"),
        sa.CheckConstraint(f"slot IN {_SLOTS}", name="ck_novel_state_slot_slot"),
        sa.CheckConstraint(f"origin IN {_SLOT_ORIGINS}", name="ck_novel_state_slot_origin"),
    )
    op.create_index(
        "ix_novel_state_slot_work", "novel_state_slots", ["work_id", "branch_id", "entity"]
    )

    op.create_table(
        "novel_state_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("branch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_no", sa.Integer, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("entity", sa.String(120), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("from_value", sa.Text, nullable=False, server_default=""),
        sa.Column("to_value", sa.Text, nullable=False),
        sa.Column("source_quote", sa.Text, nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("projected", sa.Boolean, nullable=False, server_default=sa.false()),
        # 值的机械依据（持有人名出现在引文里），或留待人工判断的原因。
        sa.Column("evidence", sa.Text, nullable=False, server_default=""),
        sa.Column("seq", sa.Integer, nullable=False, server_default="0"),
        # 同章同槽位可以有多个不同值（冲突）：唯一键必须带上值本身，否则互相覆盖。
        sa.Column("value_hash", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "work_id", "branch_id", "chapter_no", "source_hash",
            "entity", "slot", "value_hash",
            name="uq_novel_state_change",
        ),
        sa.CheckConstraint(f"slot IN {_SLOTS}", name="ck_novel_state_change_slot"),
        sa.CheckConstraint(f"origin IN {_CHANGE_ORIGINS}", name="ck_novel_state_change_origin"),
        sa.CheckConstraint(f"status IN {_CHANGE_STATUS}", name="ck_novel_state_change_status"),
    )
    op.create_index(
        "ix_novel_state_change_chapter",
        "novel_state_changes",
        ["work_id", "branch_id", "chapter_no"],
    )

    op.create_table(
        "novel_state_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("branch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_no", sa.Integer, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("entities", sa.Integer, nullable=False, server_default="0"),
        sa.Column("changes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("projected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("conflicts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "work_id", "branch_id", "chapter_no", "source_hash", name="uq_novel_state_run"
        ),
        sa.CheckConstraint(f"status IN {_RUN_STATUS}", name="ck_novel_state_run_status"),
    )
    op.create_index(
        "ix_novel_state_run_work", "novel_state_runs", ["work_id", "branch_id", "chapter_no"]
    )


def downgrade() -> None:
    op.drop_index("ix_novel_state_run_work", table_name="novel_state_runs")
    op.drop_table("novel_state_runs")
    op.drop_index("ix_novel_state_change_chapter", table_name="novel_state_changes")
    op.drop_table("novel_state_changes")
    op.drop_index("ix_novel_state_slot_work", table_name="novel_state_slots")
    op.drop_table("novel_state_slots")
    op.drop_index("ix_novel_state_entity_work", table_name="novel_state_entities")
    op.drop_table("novel_state_entities")
