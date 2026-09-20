"""Dump full F4 evidence for the known patched CANONIZED run prefix."""
from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"


def q(r, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=120).out.strip()


r = Remote()
meta = q(
    r,
    "SELECT id::text, work_id::text, state, created_at::text "
    "FROM novel_chapter_runs WHERE id::text LIKE '6e50d99a%' LIMIT 1",
)
print("meta", meta)
run_id, work_id, state, created = meta.split("|", 3)
raw = q(
    r,
    f"SELECT generation_context::text FROM novel_chapter_runs WHERE id='{run_id}'",
)
ctx = json.loads(raw)
takes = (ctx.get("production") or {}).get("takes") or []
out = {
    "work_id": work_id,
    "run_id": run_id,
    "state": state,
    "created_at": created,
    "accepted": (ctx.get("production") or {}).get("accepted"),
    "takes": [],
}
any_patch = False
for i, take in enumerate(takes):
    lp = take.get("last_patch")
    item = {
        "index": i,
        "scene_index": take.get("scene_index"),
        "status": take.get("status"),
        "revisions": take.get("revisions"),
        "last_patch": bool(lp),
        "requirements_version": take.get("requirements_version"),
        "validation_passed": (take.get("validation") or {}).get("passed"),
        "report_invalid": (take.get("validation") or {}).get("report_invalid"),
        "content_hash": (take.get("validation") or {}).get("content_hash"),
        "n_requirements": len(take.get("requirements") or []),
    }
    if lp:
        any_patch = True
        item["patch_base_hash"] = lp.get("base_content_hash")
        item["n_replacements"] = len(lp.get("replacements") or [])
        vers = take.get("prose_versions") or []
        item["prose_versions"] = len(vers)
        if len(vers) >= 2:
            a, b = str(vers[0]), str(vers[-1])
            item["prefix80_retained"] = b.startswith(a[:80]) if a else False
            item["lens"] = [len(a), len(b)]
    out["takes"].append(item)
out["same_run_patch_then_accept"] = any(
    t["last_patch"] and t["status"] == "ACCEPTED" for t in out["takes"]
)
out["canonized_with_any_patch"] = state == "CANONIZED" and any_patch
print(json.dumps(out, ensure_ascii=False, indent=2))
