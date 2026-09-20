"""在章推进期间高频抓锁现场：谁在等、等谁、持锁者当时在跑什么。

有等待就打印；没有也周期性报一次"事务>10s"的会话，便于定位持锁者。
用法：python deploy/novel/d4_lockwatch.py [次数] [间隔秒]
"""

from __future__ import annotations

import shlex
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"

WAITERS = (
    "SELECT a.pid, a.state, age(now(), a.xact_start) AS xact_age, "
    "age(now(), a.state_change) AS waited, a.wait_event, a.client_addr::text, "
    "left(regexp_replace(a.query, '\\s+', ' ', 'g'), 120) AS q "
    "FROM pg_stat_activity a WHERE a.datname = 'regent' "
    "AND a.wait_event_type = 'Lock' AND a.pid <> pg_backend_pid() "
    "ORDER BY a.state_change"
)

# 事务开启超过 10 秒的会话：持锁嫌疑
OLD_TX = (
    "SELECT a.pid, a.state, age(now(), a.xact_start) AS age, "
    "a.backend_xid IS NOT NULL AS has_xid, a.client_addr::text, "
    "left(regexp_replace(a.query, '\\s+', ' ', 'g'), 120) AS q "
    "FROM pg_stat_activity a WHERE a.datname = 'regent' "
    "AND a.xact_start IS NOT NULL AND a.xact_start < now() - interval '10 seconds' "
    "AND a.pid <> pg_backend_pid() ORDER BY a.xact_start"
)

# work 行 / run 行上的锁（含授予与未授予）
ROW_LOCKS = (
    "SELECT l.pid, l.granted, l.mode, COALESCE(c.relname, l.locktype) AS obj "
    "FROM pg_locks l LEFT JOIN pg_class c ON c.oid = l.relation "
    "WHERE c.relname IN ('novel_works','novel_chapter_runs','novel_model_calls') "
    "AND l.pid <> pg_backend_pid() ORDER BY l.pid"
)


def main() -> int:
    times = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    r = Remote()
    hits = 0
    for i in range(times):
        waiters = r.run(f"{PSQL} {shlex.quote(WAITERS)}", timeout=40).out.strip()
        old = r.run(f"{PSQL} {shlex.quote(OLD_TX)}", timeout=40).out.strip()
        if waiters:
            hits += 1
            print(f"\n===== 采样 {i + 1}：有锁等待 =====")
            for line in waiters.splitlines():
                p = line.split("|")
                if len(p) < 6:
                    print(line)
                    continue
                print(f"  等锁 pid={p[0]} {p[1]} 事务年龄={p[2]} 已等={p[3]} {p[4]} from={p[5]}")
                print(f"       {p[6] if len(p) > 6 else ''}")
            print("  -- 持锁嫌疑（事务 >10s）--")
            print("  " + (old or "(无)"))
            print("  -- 行锁 --")
            print("  " + (r.run(f"{PSQL} {shlex.quote(ROW_LOCKS)}", timeout=40).out.strip() or "(无)"))
        elif old:
            print(f"[{i + 1}] 无等待，但事务>10s: {old}")
        if i + 1 < times:
            time.sleep(gap)
    print(f"\n[采样结束] 命中锁等待 {hits} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
