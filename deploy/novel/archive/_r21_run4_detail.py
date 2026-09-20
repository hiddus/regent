import json
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

r = Remote()
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"
rid = "d9c9b787-733f-4876-882a-d37cdb21411b"
# failure reason from steps
sql = (
    "SELECT step || ':' || state || ':' || coalesce(error_code,'') "
    f"FROM novel_chapter_steps WHERE run_id = '{rid}' ORDER BY id"
)
print("STEPS", r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out)
raw = r.run(
    f"{PSQL} {shlex.quote(f'SELECT generation_context::text FROM novel_chapter_runs WHERE id = {chr(39)}{rid}{chr(39)}')}",
    timeout=180,
).out.strip()
prod = json.loads(raw)["production"]
print("phase", prod.get("phase"), "takes", len(prod.get("takes") or []))
for d in (prod.get("decisions") or [])[-12:]:
    print(
        "dec",
        d.get("phase"),
        d.get("action"),
        "rmode",
        d.get("revision_mode"),
        "take",
        d.get("take_no"),
    )
# any SceneTextPatch in call ledger?
for c in prod.get("calls") or []:
    purpose = str(c.get("purpose") or "")
    out = c.get("output") or {}
    if "replacements" in out or "base_content_hash" in out:
        print("PATCH_CALL", purpose, list(out)[:6])
    if "RENDER" in purpose:
        keys = list((c.get("output") or {}).keys())[:8]
        print("RENDER_OUT_KEYS", purpose, keys)
