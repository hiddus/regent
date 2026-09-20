"""Why run5 died after successful patch + validation."""
from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

RID = "63bcc213-7632-484e-b12a-9d861af4c391"
r = Remote()
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"
raw = r.run(
    f"{PSQL} {shlex.quote(f'SELECT generation_context::text FROM novel_chapter_runs WHERE id = {chr(39)}{RID}{chr(39)}')}",
    timeout=180,
).out.strip()
ctx = json.loads(raw)
prod = ctx["production"]
print("keys", sorted(prod.keys()))
print("phase", prod.get("phase"), "scene_index", prod.get("scene_index"))
print("accepted", prod.get("accepted"), "chapter_repairs", prod.get("chapter_repairs"))
print("last_failed_hash", prod.get("last_failed_hash"))
review = ctx.get("review") or {}
print("run.review", json.dumps(ctx.get("review") if "review" in ctx else None, ensure_ascii=False)[:300])
# chapter validation call
for c in prod.get("calls") or []:
    if c.get("schema") == "ChapterValidation":
        print("ChapterValidation", json.dumps(c.get("output"), ensure_ascii=False)[:500])
# decisions involving chapter?
for d in prod.get("decisions") or []:
    if d.get("phase") not in ("WATCH_TAKE", "WATCH_PROSE", "ACT"):
        print("other_dec", d)
# final take state machine
for i, t in enumerate(prod["takes"]):
    print(
        f"t{i}",
        "sc", t.get("scene_index"),
        "no", t.get("take_no"),
        "st", t.get("status"),
        "ss", t.get("scene_state"),
        "art", t.get("artifact"),
        "assembled", t.get("assembled"),
    )
# worker error from steps
print(
    "err",
    r.run(
        f"{PSQL} {shlex.quote(f'SELECT coalesce(error_code, chr(32)) FROM novel_chapter_steps WHERE run_id = {chr(39)}{RID}{chr(39)} AND state = {chr(39)}FAILED{chr(39)}')}",
        timeout=60,
    ).out,
)
