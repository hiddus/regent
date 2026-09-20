"""Audit whether R21 patch path actually fired on successful CANONIZED run."""
from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

WORKS = [
    ("run2_ok", "f153f4b3-5e84-4d59-870d-09fd5d328c9e"),
    ("run1_fail", "f14e8405-2008-4d94-b905-c612edd2a148"),
    ("run3_idem", "787edefc-bac0-458d-9904-603cf368eefc"),
    ("run3b_retake", "9e466871-9f94-4f03-bd2f-a188dc3827ba"),
]
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"
r = Remote()


def q(sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=180).out.strip()


for label, work in WORKS:
    print("=" * 60, label)
    rid = q(
        f"SELECT id::text FROM novel_chapter_runs WHERE work_id='{work}' ORDER BY created_at DESC LIMIT 1"
    )
    print("run", rid)
    if not rid:
        continue
    raw = q(f"SELECT generation_context::text FROM novel_chapter_runs WHERE id='{rid}'")
    if not raw:
        print("no ctx")
        continue
    ctx = json.loads(raw)
    prod = ctx.get("production") or {}
    print("phase", prod.get("phase"), "calls", prod.get("call_count"), "accepted", prod.get("accepted"))
    for i, take in enumerate(prod.get("takes") or []):
        vers = take.get("prose_versions") or []
        print(
            f"  take[{i}] rev={take.get('revisions')} mode={take.get('revision_mode')!r} "
            f"patch={bool(take.get('last_patch'))} err={take.get('patch_error')} "
            f"lens={[len(v) for v in vers]} reqs={len(take.get('requirements') or [])}"
        )
        for d in prod.get("decisions") or []:
            if d.get("take_no") == take.get("take_no") and d.get("phase") == "WATCH_PROSE":
                print(
                    f"    prose_dec action={d.get('action')} revision_mode={d.get('revision_mode')!r} "
                    f"instr={(d.get('instruction') or '')[:80]!r}"
                )
        if take.get("last_patch"):
            lp = take["last_patch"]
            print(
                "    last_patch hash",
                str(lp.get("base_content_hash", ""))[:16],
                "n_repl",
                len(lp.get("replacements") or []),
            )
        val = take.get("validation") or {}
        if val:
            print(
                "    val",
                val.get("passed"),
                "invalid",
                val.get("report_invalid"),
                "issues",
                val.get("issues"),
                "n_req_verdicts",
                len(val.get("requirements") or []),
            )
    # schema of writer calls
    schemas = []
    for c in prod.get("calls") or []:
        purpose = str(c.get("purpose") or "")
        if "执笔" in purpose or "RENDER" in purpose or "rewrite" in purpose.lower():
            schemas.append(purpose)
    print("  writer-ish purposes:", schemas[-8:])
