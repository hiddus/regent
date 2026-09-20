"""Inspect F4 work run/decision state."""
from __future__ import annotations

import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote  # noqa: E402

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
WORK = sys.argv[1]


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


r = Remote()
print("runs:")
print(
    q(
        r,
        "SELECT id::text, state, current_step, updated_at::text "
        f"FROM novel_chapter_runs WHERE work_id='{WORK}' ORDER BY created_at DESC LIMIT 3",
    )
)
print("decisions cols sample:")
print(
    q(
        r,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='novel_decisions' ORDER BY ordinal_position",
    )
)
print("decisions:")
print(
    q(
        r,
        f"SELECT * FROM novel_decisions WHERE work_id='{WORK}' ORDER BY created_at DESC LIMIT 3",
    )[:2000]
)
