"""Inspect work run state and patch flags for F4."""
from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
WORK = sys.argv[1]


def q(r, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


r = Remote()
print("work:", q(r, f"SELECT id::text, state FROM novel_works WHERE id='{WORK}'"))
print(
    "runs:",
    q(
        r,
        "SELECT id::text, state, current_step, updated_at::text "
        f"FROM novel_chapter_runs WHERE work_id='{WORK}' ORDER BY created_at DESC LIMIT 3",
    ),
)
raw = q(
    r,
    "SELECT generation_context::text FROM novel_chapter_runs "
    f"WHERE work_id='{WORK}' ORDER BY created_at DESC LIMIT 1",
)
if not raw:
    print("no context")
    raise SystemExit(0)
ctx = json.loads(raw)
prod = ctx.get("production") or {}
takes = prod.get("takes") or []
print("accepted", prod.get("accepted"), "n_takes", len(takes))
for i, t in enumerate(takes):
    print(
        f"take[{i}] scene={t.get('scene_index')} status={t.get('status')} "
        f"rev={t.get('revisions')} patch={bool(t.get('last_patch'))} "
        f"req_ver={t.get('requirements_version')} "
        f"val={(t.get('validation') or {}).get('passed')}"
    )
