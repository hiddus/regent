"""列出各作品的角色名，并核对最近 plan 产出用到的名字是否在册。只读。"""

from __future__ import annotations

import json
import shlex
import sys

from _ssh import Remote

PSQL = 'docker exec -i regent-postgres psql -U regent -d regent -t -A -F "|" -c'


def q(r: Remote, sql: str) -> str:
    return r.run(f'{PSQL} {shlex.quote(" ".join(sql.split()))}', timeout=120).out


def main() -> int:
    r = Remote()
    print("=== 角色名（按作品）===")
    rows = q(
        r,
        """
        SELECT left(p.work_id::text,8) || '|' || p.name
        FROM novel_personas p
        ORDER BY p.work_id, p.name
        """,
    )
    cast_by_work: dict[str, set[str]] = {}
    for line in rows.strip().splitlines():
        if "|" not in line:
            continue
        w, name = line.split("|", 1)
        print(f"  {w}  {name}")
        cast_by_work.setdefault(w, set()).add(name)
    print(f"合计 {len(cast_by_work)} 个作品")

    print("\n=== 最近 plan 产出用到的名字 ===")
    out = q(
        r,
        """
        SELECT left(work_id::text,8), output_json::text
        FROM novel_model_calls
        WHERE purpose='plan' AND output_json IS NOT NULL
        ORDER BY created_at DESC LIMIT 3
        """,
    )
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        w, _, payload = line.partition("|")
        try:
            data = json.loads(payload)
        except Exception:  # noqa: BLE001
            continue
        names: list[str] = []
        for scene in data.get("scenes") or []:
            names += [a.get("persona") for a in scene.get("actors") or []]
        known = cast_by_work.get(w, set())
        verdict = ["  OK " if n in known else "  ✗  " for n in names]
        print(f"  作品 {w}: {names}")
        print(f"    在册 {sorted(known)}")
        print(f"    判定 {''.join(v[0] for v in verdict) or '(无角色)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
