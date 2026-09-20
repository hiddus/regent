import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

WID = sys.argv[1]
PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
r = Remote()


def q(sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


print("work", q(f"SELECT state, latest_chapter_no FROM novel_works WHERE id='{WID}'"))
print(
    "calls_by_status",
    q(
        f"SELECT status, count(*) FROM novel_model_calls WHERE work_id='{WID}' GROUP BY 1 ORDER BY 1"
    ),
)
print(
    "call_count_ctx",
    q(
        "SELECT generation_context->'production'->>'call_count' "
        f"FROM novel_chapter_runs WHERE work_id='{WID}' ORDER BY created_at DESC LIMIT 1"
    ),
)
print(
    "takes",
    q(
        "SELECT jsonb_array_length(COALESCE(generation_context->'production'->'takes','[]'::jsonb)) "
        f"FROM novel_chapter_runs WHERE work_id='{WID}' ORDER BY created_at DESC LIMIT 1"
    ),
)
