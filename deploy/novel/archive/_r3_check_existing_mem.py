import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
r = Remote()


def q(sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


ids = [
    "c693853f-824a-4545-b43f-8585c7cc505f",
    "08545a4a-c2b9-4487-9871-d6b34e214f62",
    "d814b81f-9a8a-485e-afa3-7778a1d05fa7",
    "3c969bd6-2149-4739-a811-d677992932e4",
]
print("global_items", q("SELECT count(*) FROM novel_memory_items"))
for wid in ids:
    print(
        "---",
        wid[:8],
        q(
            f"SELECT w.state, w.latest_chapter_no, "
            f"(SELECT count(*) FROM novel_memory_items m WHERE m.work_id=w.id) "
            f"FROM novel_works w WHERE w.id='{wid}'"
        ),
    )
    print(
        "  by_ch",
        q(
            f"SELECT source_chapter_no, count(*) FROM novel_memory_items "
            f"WHERE work_id='{wid}' GROUP BY 1 ORDER BY 1"
        )
        or "(none)",
    )
