"""Verify run2 prose versions / patch usage for R21 acceptance."""
from __future__ import annotations

import hashlib
import json
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

WORK = "f153f4b3-5e84-4d59-870d-09fd5d328c9e"
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"
r = Remote()
sql = f"SELECT generation_context::text FROM novel_chapter_runs WHERE work_id = '{WORK}' ORDER BY created_at DESC LIMIT 1"
raw = r.run(f"{PSQL} {shlex.quote(sql)}", timeout=180).out.strip()
ctx = json.loads(raw)
prod = ctx["production"]
print("accepted", prod.get("accepted"), "calls", prod.get("call_count"))
for i, take in enumerate(prod.get("takes") or []):
    vers = take.get("prose_versions") or []
    lens = [len(v) for v in vers]
    print(f"take[{i}] revisions={take.get('revisions')} lens={lens} reqs={len(take.get('requirements') or [])}")
    if len(vers) >= 2:
        a, b = vers[0], vers[1]
        # 若用了补丁合并，前缀应大量保留（非 3263→511 式截断）
        prefix = a[: min(400, len(a) // 3)]
        print("  prefix_retained", prefix in b, "shrink_ratio", round(len(b) / max(len(a), 1), 3))
        print("  last_patch", bool(take.get("last_patch")))
        print("  content_hash", hashlib.sha256((take.get("content") or "").encode()).hexdigest()[:16])
    val = take.get("validation") or {}
    if val:
        print("  validation", val.get("passed"), "invalid", val.get("report_invalid"), "issues", val.get("issues"))
