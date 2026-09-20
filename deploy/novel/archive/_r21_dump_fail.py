"""Dump R21 ch1 failure details. Read-only."""
from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote  # noqa: E402

WORK = "f14e8405-2008-4d94-b905-c612edd2a148"
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(' '.join(sql.split()))}", timeout=180).out


def main() -> int:
    r = Remote()
    meta = q(
        r,
        f"""
        SELECT id::text || E'\\t' || status || E'\\t' || coalesce(failure_reason,'')
        FROM novel_chapter_runs WHERE work_id = '{WORK}'
        ORDER BY created_at DESC LIMIT 1
        """,
    ).strip()
    print("META", meta)
    rid = meta.split("\t")[0]
    raw = q(r, f"SELECT generation_context::text FROM novel_chapter_runs WHERE id = '{rid}'").strip()
    ctx = json.loads(raw)
    prod = ctx.get("production") or {}
    print("phase", prod.get("phase"), "scene", prod.get("scene_index"))
    take = (prod.get("takes") or [{}])[-1]
    print("revisions", take.get("revisions"), "versions", [len(x) for x in take.get("prose_versions") or []])
    print("revision_mode", take.get("revision_mode"), "patch_error", take.get("patch_error"))
    print("requirements", len(take.get("requirements") or []))
    print("validation", json.dumps(take.get("validation"), ensure_ascii=False)[:800] if take.get("validation") else None)
    print("conflict", take.get("revision_conflict_notices"))
    # last prose decisions
    for d in (prod.get("decisions") or [])[-6:]:
        if d.get("phase") in ("WATCH_PROSE", "WATCH_TAKE"):
            print(
                "decision",
                d.get("phase"),
                d.get("action"),
                "ev0",
                (d.get("evidence") or [""])[0][:60],
            )
    # last stop reason in calls?
    print("call_count", prod.get("call_count"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
