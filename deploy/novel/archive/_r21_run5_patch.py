"""Inspect run5 for last_patch / catastrophic loss / validate failure."""
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
prod = json.loads(raw)["production"]
print("phase", prod.get("phase"), "scene", prod.get("scene_index"), "accepted", prod.get("accepted"))
print("calls", prod.get("call_count"))

for i, take in enumerate(prod.get("takes") or []):
    vers = take.get("prose_versions") or []
    print("=" * 40, f"take[{i}]")
    print(
        "take_no", take.get("take_no"), "scene", take.get("scene_index"),
        "status", take.get("status"), "rev", take.get("revisions"),
        "mode", take.get("revision_mode"),
    )
    print("lens", [len(v) for v in vers], "content", len(take.get("content") or ""))
    print("last_patch", bool(take.get("last_patch")), "patch_error", take.get("patch_error"))
    if take.get("last_patch"):
        lp = take["last_patch"]
        print("  hash", str(lp.get("base_content_hash"))[:16], "n_repl", len(lp.get("replacements") or []))
        for rep in (lp.get("replacements") or [])[:4]:
            print("  repl ids", rep.get("paragraph_ids"), "text_len", len(rep.get("text") or ""))
    # prefix retention if 2 versions
    if len(vers) >= 2:
        a, b = vers[0], vers[1]
        head = a[:80]
        print("  prefix80_in_v2", head in b, "ratio", round(len(b) / max(len(a), 1), 3))
    val = take.get("validation")
    if val:
        print(
            "  validation passed", val.get("passed"),
            "invalid", val.get("report_invalid"),
            "issues", val.get("issues"),
            "n_req", len(val.get("requirements") or []),
        )
    print("  requirements_frozen", len(take.get("requirements") or []))

# schema names in calls
schemas = {}
for c in prod.get("calls") or []:
    name = str(c.get("schema") or "?")
    schemas[name] = schemas.get(name, 0) + 1
print("SCHEMAS", schemas)
# any SceneTextPatch outputs
for c in prod.get("calls") or []:
    if c.get("schema") == "SceneTextPatch" or "replacements" in (c.get("output") or {}):
        out = c.get("output") or {}
        print("PATCH_CALL", c.get("purpose"), "n_repl", len(out.get("replacements") or []))
