"""缺陷 4 现场取证：锁泄漏 / 孤儿 RESERVED / 重试预算。

只做只读取证，不改动任何生产状态。
用法：python deploy/novel/d4_diag.py
"""

from __future__ import annotations

import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"

QUERIES = [
    (
        "事务中空闲连接数（锁泄漏源头）",
        "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction'",
    ),
    (
        "活跃/阻塞中的会话",
        "SELECT pid, state, age(now(), xact_start) AS xact_age, "
        "left(regexp_replace(query, '\\s+', ' ', 'g'), 80) AS q "
        "FROM pg_stat_activity WHERE datname = 'regent' AND state <> 'idle' "
        "ORDER BY xact_start LIMIT 12",
    ),
    (
        "调用按状态分布",
        "SELECT status, count(*), min(created_at)::text, max(created_at)::text "
        "FROM novel_model_calls GROUP BY status ORDER BY 2 DESC",
    ),
    (
        "RESERVED 孤儿：租约字段是否为空（空 = 回收逻辑永远看不见）",
        "SELECT status, "
        "count(*) FILTER (WHERE lease_expires_at IS NULL) AS null_lease, "
        "count(*) FILTER (WHERE lease_expires_at IS NOT NULL) AS has_lease "
        "FROM novel_model_calls GROUP BY 1",
    ),
    (
        "未收口调用明细",
        "SELECT logical_call_id, status, error_code, reconcile_count, "
        "lease_expires_at::text, created_at::text, updated_at::text "
        "FROM novel_model_calls WHERE status IN ('RESERVED','UNKNOWN') "
        "ORDER BY created_at LIMIT 10",
    ),
    (
        "最近章运行",
        "SELECT id, state, current_step, version, updated_at::text "
        "FROM novel_chapter_runs ORDER BY updated_at DESC LIMIT 6",
    ),
    (
        "当前库级空闲事务超时设置（0 = 永不超时 = 泄漏可永久存活）",
        "SHOW idle_in_transaction_session_timeout",
    ),
]


def main() -> int:
    r = Remote()
    for label, sql in QUERIES:
        print(f"\n=== {label} ===")
        out = r.run(f"{PSQL} {shlex.quote(sql)}").out.strip()
        print(out if out else "(空)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
