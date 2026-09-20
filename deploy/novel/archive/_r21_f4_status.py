"""Inspect work run state for F4 continuation."""
from __future__ import annotations

import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
WORK = sys.argv[1]


def q(r, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


r = Remote()
print(
    q(
        r,
        "SELECT id::text, state, current_step, updated_at::text "
        f"FROM novel_chapter_runs WHERE work_id='{WORK}' ORDER BY created_at DESC LIMIT 3",
    )
    or "(no runs)"
)
print(
    "pending decisions:",
    q(
        r,
        "SELECT id::text, state, COALESCE(default_option_id,'') "
        f"FROM novel_decision_requests WHERE work_id='{WORK}' "
        "AND upper(state)='PENDING' ORDER BY created_at",
    )
    or "(none)",
)
