"""Poll work after decision resolved."""
from __future__ import annotations

import json
import shlex
import sys
import time

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote
from run_chapter1 import dump_diagnostics

WORK = "ca565eb3-4cb6-4e47-be69-410afa00cdb3"
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=120).out.strip()


def main() -> int:
    limit = 1800
    with Remote() as r:
        deadline = time.time() + limit
        last = ""
        while time.time() < deadline:
            time.sleep(20)
            row = q(
                r,
                "SELECT state || '|' || coalesce(current_step,'') "
                f"FROM novel_chapter_runs WHERE work_id='{WORK}' "
                "ORDER BY created_at DESC LIMIT 1",
            )
            if row != last:
                print(time.strftime("%H:%M:%S"), row, flush=True)
                last = row
            state = row.split("|")[0] if row else "?"
            if state == "CANONIZED":
                print("[OK] CANONIZED", flush=True)
                rid = q(
                    r,
                    f"SELECT id::text FROM novel_chapter_runs WHERE work_id='{WORK}' "
                    "ORDER BY created_at DESC LIMIT 1",
                )
                raw = q(
                    r,
                    f"SELECT generation_context::text FROM novel_chapter_runs WHERE id='{rid}'",
                )
                prod = json.loads(raw)["production"]
                for i, take in enumerate(prod.get("takes") or []):
                    vers = take.get("prose_versions") or []
                    print(
                        f"take[{i}] sc={take.get('scene_index')} st={take.get('status')} "
                        f"rev={take.get('revisions')} patch={bool(take.get('last_patch'))} "
                        f"lens={[len(v) for v in vers]}",
                        flush=True,
                    )
                    if len(vers) >= 2 and take.get("last_patch"):
                        print(
                            "  prefix_kept",
                            vers[0][:60] in vers[1],
                            "ratio",
                            round(len(vers[1]) / max(len(vers[0]), 1), 3),
                            flush=True,
                        )
                return 0
            if state in ("TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
                print("[FAIL]", state, flush=True)
                dump_diagnostics(r, WORK)
                return 1
        print("[FAIL] timeout", last, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
