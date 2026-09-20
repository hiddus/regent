"""After a chapter run, dump same-run patch→validate→accept evidence (R21-F4)."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote  # noqa: E402

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"


def main() -> int:
    work_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not work_id:
        print("usage: _r21_f4_evidence.py <work_id>")
        return 2
    r = Remote()
    row = r.run(
        f"{PSQL} \"SELECT id::text, state, generation_context::text "
        f"FROM novel_chapter_runs WHERE work_id='{work_id}' "
        f"ORDER BY created_at DESC LIMIT 1\"",
        timeout=60,
    ).out.strip()
    if not row:
        print("no run")
        return 1
    run_id, state, raw = row.split("|", 2)
    ctx = json.loads(raw)
    prod = ctx.get("production") or {}
    takes = prod.get("takes") or []
    evidence = {
        "work_id": work_id,
        "run_id": run_id,
        "state": state,
        "accepted": prod.get("accepted"),
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
            "patch_error": take.get("patch_error"),
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
                item["prefix_retained"] = str(vers[-1]).startswith(str(vers[0])[:80])
        evidence["takes"].append(item)
    evidence["same_run_patch_then_accept"] = any(
        t["last_patch"] and t["status"] == "ACCEPTED" for t in evidence["takes"]
    )
    evidence["canonized_with_any_patch"] = state == "CANONIZED" and any_patch
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
