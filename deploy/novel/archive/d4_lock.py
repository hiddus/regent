"""定位「事务中空闲」泄漏源：谁在持锁、持了多久、来自哪个容器/哪段代码。

只读。用法：python deploy/novel/d4_lock.py
"""

from __future__ import annotations

import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"

QUERIES = [
    (
        "泄漏会话全貌（完整 SQL + 来源 + 事务年龄）",
        "SELECT a.pid, a.state, age(now(), a.xact_start) AS xact_age, "
        "a.client_addr, a.application_name, a.backend_type, "
        "regexp_replace(a.query, '\\s+', ' ', 'g') AS full_query "
        "FROM pg_stat_activity a WHERE a.datname = 'regent' "
        "AND a.state = 'idle in transaction' ORDER BY a.xact_start",
    ),
    (
        "谁在等谁（阻塞链）",
        "SELECT w.pid AS waiting_pid, w.state, "
        "age(now(), w.xact_start) AS waited, "
        "regexp_replace(w.query, '\\s+', ' ', 'g') AS waiting_query, "
        "count(l.pid) AS blockers, "
        "string_agg(DISTINCT l.pid::text || '/' || l.state, ', ') AS blocker_pids "
        "FROM pg_stat_activity w "
        "LEFT JOIN pg_locks wl ON wl.pid = w.pid AND NOT wl.granted "
        "LEFT JOIN pg_locks ol ON ol.locktype = wl.locktype AND ol.database IS NOT DISTINCT FROM wl.database "
        "AND ol.relation IS NOT DISTINCT FROM wl.relation AND ol.page IS NOT DISTINCT FROM wl.page "
        "AND ol.tuple IS NOT DISTINCT FROM wl.tuple AND ol.virtualxid IS NOT DISTINCT FROM wl.virtualxid "
        "AND ol.transactionid IS NOT DISTINCT FROM wl.transactionid "
        "AND ol.classid IS NOT DISTINCT FROM wl.classid AND ol.objid IS NOT DISTINCT FROM wl.objid "
        "AND ol.objsubid IS NOT DISTINCT FROM wl.objsubid AND ol.pid <> w.pid AND ol.granted "
        "LEFT JOIN pg_stat_activity l ON l.pid = ol.pid "
        "WHERE w.datname = 'regent' AND w.state <> 'idle' AND w.pid <> pg_backend_pid() "
        "GROUP BY 1,2,3,4 ORDER BY waited DESC LIMIT 10",
    ),
    (
        "novel 表上的未授予锁",
        "SELECT l.pid, l.locktype, l.mode, l.granted, "
        "COALESCE(c.relname, l.locktype) AS obj "
        "FROM pg_locks l LEFT JOIN pg_class c ON c.oid = l.relation "
        "WHERE NOT l.granted ORDER BY l.pid",
    ),
]


def main() -> int:
    r = Remote()
    for label, sql in QUERIES:
        print(f"\n=== {label} ===")
        out = r.run(f"{PSQL} {shlex.quote(sql)}").out.strip()
        print(out if out else "(空——没有泄漏/没有阻塞)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
