from __future__ import annotations

import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
WORK = "d814b81f-9a8a-485e-afa3-7778a1d05fa7"


def q(r, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


r = Remote()
print("tables:")
print(q(r, "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE '%decision%'"))
print("all novel:")
print(q(r, "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'novel_%' ORDER BY 1"))
print("run ctx keys:")
print(
    q(
        r,
        "SELECT jsonb_object_keys(generation_context) FROM novel_chapter_runs "
        f"WHERE work_id='{WORK}' LIMIT 20",
    )
)
