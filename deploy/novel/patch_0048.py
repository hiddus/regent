"""重写 0048 迁移：冻结 0048-era schema，不再直接用当前 ORM metadata 建表。"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, "core/src")

TARGET = "core/migrations/versions/20260903_0048_novel_domain.py"

HEADER = '''"""Novel Engine 领域 schema（M0 首个开发批次 · Plan §10 第 3/4/6 项）。

Revision ID: 20260903_0048
Revises: 20260810_0047

覆盖：
- 身份与会话（G-11：服务端 principal，不采信客户端 actor）
- 作品 / 目标 / 关键路径 / onboarding（FR-01~FR-05）
- 章运行与步骤（Tech-Spec §3.3 / §5 幂等键）
- 角色 / 信息集 / Canon（G-03 / G-06 / G-07）
- 裁决 / 内容审核（G-13 / G-23 / FR-25）
- 分享 / 导出 / 导出告知（G-15 / G-22 / FR-17 / FR-23）
- 模型调用 / 成本 / 额度（G-08 / G-10 / FR-18 / FR-19）
- 持久事件 / 序列 / 幂等记录（G-09 / G-17 / FR-20）

P0-5（真实 PostgreSQL 认证）修正：本迁移**不再**直接按当前 ORM metadata 全量建表。
metadata 是「今天」的形状，而 0049~0052 还要各自建 novel_volumes / novel_arc_nodes
以及一批列与约束——照抄 metadata 会让全新库在升级链上撞 DuplicateTable /
DuplicateColumn，即**全新环境根本装不起来**。

因此本迁移：建表 → 归一化回 0048 时代的形状 → 用冻结清单核对。模型再漂移时这里
会**显式报错**，而不是静默建出一个与升级库不一致的库。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0048"
down_revision: str | None = "20260810_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- 归后续迁移所有的对象：0048 不得抢先创建 ---------------------------------
_LATER_TABLES = frozenset({"novel_volumes", "novel_arc_nodes"})

_LATER_COLUMNS: dict[str, frozenset[str]] = {
    "novel_works": frozenset({"total_volume_count"}),                       # 0049
    "novel_critical_nodes": frozenset({"volume_no", "arc_no"}),             # 0049
    "novel_chapter_runs": frozenset(                                        # 0050 / 0051
        {
            "user_guidance",
            "auto_advance",
            "lease_owner",
            "lease_expires_at",
            "fencing_token",
        }
    ),
    "novel_model_calls": frozenset(                                         # 0051 / 0052
        {
            "attempt",
            "reserved_amount_minor",
            "actual_amount_minor",
            "currency",
            "cached_input_tokens",
            "usage_source",
            "provider_request_id",
            "reconcile_count",
            "lease_owner",
            "lease_expires_at",
            "lease_ttl_seconds",
            "output_json",
            "error_code",
            "updated_at",
        }
    ),
}

# 0051 会重建的索引与唯一约束：0048 只能是 0051 之前那一版
_LATER_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_novel_model_calls_logical", "novel_model_calls"),
    ("ix_novel_model_calls_status", "novel_model_calls"),
)

'''

TAIL = '''

def _metadata_tables() -> list[sa.Table]:
    from regent.novel.infrastructure.models import NovelBase

    return [
        table
        for table in NovelBase.metadata.sorted_tables
        if table.name not in _LATER_TABLES
    ]


def _assert_frozen(bind: sa.Connection) -> None:
    """建成后核对：与冻结清单不一致就显式失败，不留静默漂移。"""
    inspector = sa.inspect(bind)
    for name in sorted(_FROZEN_COLUMNS):
        actual = {column["name"] for column in inspector.get_columns(name)}
        expected = set(_FROZEN_COLUMNS[name])
        if actual != expected:
            raise RuntimeError(
                f"0048 schema drift on {name}: "
                f"missing={sorted(expected - actual)} "
                f"unexpected={sorted(actual - expected)}; "
                "模型改了但没有迁移——不要改 0048，请新增迁移"
            )


def upgrade() -> None:
    bind = op.get_bind()
    for table in _metadata_tables():
        table.create(bind)

    # 归一化：把「今天」多出来的列与约束退回 0048 时代的形状，交给 0049+ 去加。
    for table_name in sorted(_LATER_COLUMNS):
        for column in sorted(_LATER_COLUMNS[table_name]):
            op.drop_column(table_name, column)
    for index_name, table_name in _LATER_INDEXES:
        op.drop_index(index_name, table_name=table_name)

    op.drop_constraint(
        "uq_novel_model_calls_logical_attempt", "novel_model_calls", type_="unique"
    )
    op.create_unique_constraint(
        "uq_novel_model_calls_logical", "novel_model_calls", ["logical_call_id"]
    )
    op.drop_constraint("uq_novel_cost_settlement", "novel_cost_entries", type_="unique")
    op.create_unique_constraint(
        "uq_novel_cost_settlement",
        "novel_cost_entries",
        ["logical_call_id", "funding_pool"],
    )

    _assert_frozen(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_metadata_tables()):
        table.drop(bind)
'''


def main() -> int:
    with open("deploy/novel/_frozen.txt", encoding="utf-8") as fh:
        frozen = fh.read()
    frozen = frozen.split("\n# 由 0049+ 创建的索引：")[0].rstrip() + "\n"

    body = HEADER + frozen + TAIL
    # 语法自检：能被编译才算写成功
    compile(body, TARGET, "exec")
    with open(TARGET, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    with open(TARGET, encoding="utf-8") as fh:
        written = fh.read()
    if written != body:
        raise SystemExit("回读不一致")
    if "NovelBase.metadata.sorted_tables" not in written:
        raise SystemExit("缺少 metadata 引用")
    if len(re.findall(r"^def upgrade", written, flags=re.M)) != 1:
        raise SystemExit("upgrade 数量不为 1")
    print(f"ok: {TARGET} ({len(written.splitlines())} 行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
