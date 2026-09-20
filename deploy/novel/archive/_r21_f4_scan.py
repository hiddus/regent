"""Scan recent runs for last_patch evidence — all work inside container."""
from __future__ import annotations

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

CODE = r'''
import json, subprocess
sql = """SELECT id::text, work_id::text, state, generation_context::text
FROM novel_chapter_runs
WHERE created_at > now() - interval '3 days'
ORDER BY created_at DESC LIMIT 30"""
raw = subprocess.check_output(
    ["docker", "exec", "regent-postgres", "psql", "-U", "regent", "-d", "regent", "-tA", "-F", "\t", "-c", sql],
    text=True, errors="replace",
)
hits = []
for line in raw.splitlines():
    if not line.strip():
        continue
    parts = line.split("\t", 3)
    if len(parts) < 4:
        continue
    run_id, work_id, state, blob = parts
    try:
        ctx = json.loads(blob)
    except Exception:
        continue
    takes = (ctx.get("production") or {}).get("takes") or []
    patched = []
    for i, t in enumerate(takes):
        if not t.get("last_patch"):
            continue
        patched.append({
            "i": i,
            "status": t.get("status"),
            "revisions": t.get("revisions"),
            "scene": t.get("scene_index"),
            "n_repl": len((t.get("last_patch") or {}).get("replacements") or []),
        })
    if not patched and state != "CANONIZED":
        continue
    hits.append({
        "run_id": run_id[:8],
        "work_id": work_id[:8],
        "state": state,
        "n_takes": len(takes),
        "patched": patched,
        "patch_then_accept": any(p["status"] == "ACCEPTED" for p in patched),
    })
print(json.dumps(hits, ensure_ascii=False, indent=2))
'''


def main() -> int:
    r = Remote()
    r.write_text("/tmp/_f4_scan.py", CODE)
    # run on host (has docker)
    out = r.run("python3 /tmp/_f4_scan.py", timeout=180)
    print("code", out.code)
    print(out.out or out.err)
    if out.code != 0:
        out2 = r.run("python /tmp/_f4_scan.py", timeout=180)
        print("retry", out2.code)
        print(out2.out or out2.err)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
