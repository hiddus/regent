"""Unblock TERMINAL_FAILED latest run so a new attempt can start (ops recovery)."""
from __future__ import annotations

import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

WID = sys.argv[1]
PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
r = Remote()


def q(sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


print(
    "before",
    q(
        f"SELECT id::text, chapter_no, attempt, state FROM novel_chapter_runs "
        f"WHERE work_id='{WID}' ORDER BY chapter_no DESC, attempt DESC LIMIT 5"
    ),
)
print(
    "latest_chapter",
    q(f"SELECT latest_chapter_no, state FROM novel_works WHERE id='{WID}'"),
)
# Only cancel the tip TERMINAL_FAILED so start_run can open the same chapter again.
print(
    q(
        f"UPDATE novel_chapter_runs SET state='CANCELLED', updated_at=now() "
        f"WHERE work_id='{WID}' AND state='TERMINAL_FAILED' "
        f"AND chapter_no=(SELECT max(chapter_no) FROM novel_chapter_runs WHERE work_id='{WID}') "
        f"RETURNING id::text, chapter_no, attempt, state"
    )
)
print(
    "after",
    q(
        f"SELECT id::text, chapter_no, attempt, state FROM novel_chapter_runs "
        f"WHERE work_id='{WID}' ORDER BY chapter_no DESC, attempt DESC LIMIT 5"
    ),
)
