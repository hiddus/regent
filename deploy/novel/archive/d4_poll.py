"""对「事务中空闲」做时间采样：看它是被谁持有、持多久、是否自己恢复。

只读。用法：python deploy/novel/d4_poll.py [次数] [间隔秒]
"""

from __future__ import annotations

import shlex
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"

SQL = (
    "SELECT pid::text, state, backend_xid IS NOT NULL AS has_xid, "
    "age(now(), xact_start) AS xact_age, age(now(), state_change) AS since_change, "
    "wait_event_type, wait_event, client_addr::text, "
    "left(regexp_replace(query, '\\s+', ' ', 'g'), 60) AS q "
    "FROM pg_stat_activity WHERE datname = 'regent' "
    "AND (state = 'idle in transaction' OR wait_event IS NOT NULL) "
    "AND pid <> pg_backend_pid() ORDER BY xact_start"
)


def main() -> int:
    times = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
    r = Remote()
    seen: dict[str, str] = {}
    for i in range(times):
        print(f"\n----- 采样 {i + 1}/{times} -----")
        out = r.run(f"{PSQL} {shlex.quote(SQL)}").out.strip()
        if not out:
            print("(无事务中空闲 / 无等待)")
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) < 8:
                print(line)
                continue
            pid, state, has_xid, xage, schg, wtype, wev, addr = parts[:8]
            q = parts[8] if len(parts) > 8 else ""
            mark = ""
            if pid in seen and seen[pid] == xage:
                mark = "  <== 事务年龄没变（卡住）"
            elif pid in seen:
                mark = "  (事务年龄在变)"
            seen[pid] = xage
            print(
                f"pid={pid} {state} xid={has_xid} xact_age={xage} "
                f"since_change={schg} wait={wtype}/{wev} from={addr}{mark}"
            )
            print(f"    q={q}")
        if i + 1 < times:
            time.sleep(gap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
