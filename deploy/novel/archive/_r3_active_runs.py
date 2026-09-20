import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

r = Remote()
PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"


def q(sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


print(
    "active_runs",
    q(
        "SELECT left(work_id::text,8), chapter_no, attempt, state, current_step, "
        "updated_at::text FROM novel_chapter_runs "
        "WHERE state IN ('QUEUED','RUNNING','RETRYABLE_FAILED','PENDING_DECISION') "
        "ORDER BY updated_at DESC LIMIT 10"
    )
    or "(none)",
)
print(
    "api_ok",
    r.run(
        "docker exec regent-api python -c \"import urllib.request; "
        "print(urllib.request.urlopen('http://127.0.0.1:8000/v1/novel/auth/session?subject=ping&display_name=p', timeout=10).status)\"",
        timeout=30,
    ).out,
)
