"""Diagnose latest TERMINAL_FAILED runs: why RETAKE exhausts before REWRITE."""
from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

r = Remote()
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -F'|' -c"

sql = (
    "SELECT id::text, state, coalesce(current_step,''), work_id::text, created_at::text "
    "FROM novel_chapter_runs WHERE state LIKE '%FAIL%' "
    "ORDER BY created_at DESC LIMIT 3"
)
print("FAILS:")
print(r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out)

for rid in [
    x.split("|")[0]
    for x in r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip().splitlines()
    if x.strip()
][:2]:
    print("=" * 50, rid)
    steps = r.run(
        f"{PSQL} {shlex.quote('SELECT step || chr(58) || state || chr(58) || coalesce(error_code, chr(32)) FROM novel_chapter_steps WHERE run_id = chr(39) || chr(39) ')}",
        timeout=30,
    )
    # simpler
    st = (
        f"SELECT step, state, coalesce(error_code,''), attempt "
        f"FROM novel_chapter_steps WHERE run_id = '{rid}' ORDER BY id"
    )
    print("STEPS", r.run(f"{PSQL} {shlex.quote(st)}", timeout=60).out)
    raw = r.run(
        f"{PSQL} {shlex.quote(f'SELECT generation_context::text FROM novel_chapter_runs WHERE id = {chr(39)}{rid}{chr(39)}')}",
        timeout=180,
    ).out.strip()
    if not raw:
        continue
    prod = json.loads(raw).get("production") or {}
    print("phase", prod.get("phase"), "scene", prod.get("scene_index"), "calls", prod.get("call_count"))
    print("n_takes", len(prod.get("takes") or []), "accepted", prod.get("accepted"))
    for i, take in enumerate(prod.get("takes") or []):
        print(
            f"  take[{i}] no={take.get('take_no')} status={take.get('status')} "
            f"rules={take.get('rule_issues')} rev={take.get('revisions')} "
            f"events={len(take.get('events') or [])} prose={len(take.get('content') or '')}"
        )
    for d in prod.get("decisions") or []:
        if d.get("phase") in ("WATCH_TAKE", "WATCH_PROSE"):
            print(
                f"  dec {d.get('phase')} a={d.get('action')} take={d.get('take_no')} "
                f"obs={(d.get('observation') or '')[:60]!r}"
            )
            if d.get("revised_brief"):
                print("    brief.purpose", (d["revised_brief"].get("purpose") or "")[:80])
