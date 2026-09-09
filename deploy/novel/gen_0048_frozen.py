"""生成 0048 迁移的冻结列清单（一次性工具）。

0048 原本直接 ``NovelBase.metadata`` 全量建表，导致 0049~0052 再建一次同样的
表/列时撞 ``DuplicateTable`` / ``DuplicateColumn``。本脚本按「0048 时代应当拥有的
列」生成冻结清单，供迁移在建成后核对，模型再漂移就显式报错而不是静默建错。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "core/src")

from regent.novel.infrastructure.models import NovelBase  # noqa: E402

# 后续迁移各自拥有的对象，0048 不得先建出来
LATER_TABLES = {"novel_volumes", "novel_arc_nodes"}
LATER_COLUMNS = {
    "novel_works": {"total_volume_count"},
    "novel_critical_nodes": {"volume_no", "arc_no"},
    "novel_chapter_runs": {
        "user_guidance", "auto_advance",
        "lease_owner", "lease_expires_at", "fencing_token",
    },
    "novel_model_calls": {
        "attempt", "reserved_amount_minor", "actual_amount_minor", "currency",
        "cached_input_tokens", "usage_source", "provider_request_id",
        "reconcile_count", "lease_owner", "lease_expires_at", "lease_ttl_seconds",
        "output_json", "error_code", "updated_at",
    },
}

lines = []
for table in NovelBase.metadata.sorted_tables:
    if table.name in LATER_TABLES:
        continue
    drop = LATER_COLUMNS.get(table.name, set())
    cols = tuple(sorted(c.name for c in table.columns if c.name not in drop))
    lines.append((table.name, cols))

print("_FROZEN_COLUMNS: dict[str, frozenset[str]] = {")
for name, cols in lines:
    print(f'    "{name}": frozenset({{')
    for c in cols:
        print(f'        "{c}",')
    print("    }),")
print("}")

# 0048 之后由其它迁移创建的索引：0048 不得先建
print()
print("# 由 0049+ 创建的索引：")
for table in NovelBase.metadata.sorted_tables:
    for idx in table.indexes:
        print(f"#   {table.name}.{idx.name}")
