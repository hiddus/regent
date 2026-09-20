"""Poll work 50177b33 status after SSH drop; dump patch evidence if done."""
from __future__ import annotations

import json
import shlex
import sys
import time

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

WORK = "50177b33-bb97-455d-b9ed-48a9c9ddd5ec"
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=120).out.strip()


def main() -> int:
    r = Remote()
    for i in range(60):
        row = q(
            r,
            f"SELECT status || '|' || coalesce(current_step,'') || '|' || id::text "
            f"FROM novel_chapter_runs WHERE work_id='{WORK}' ORDER BY created_at DESC LIMIT 1",
        )
        print(time.strftime("%H:%M:%S"), row or "(none)")
        if not row:
            time.sleep(20)
            continue
        status = row.split("|")[0]
        if status in ("CANONIZED", "TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
            rid = row.split("|")[2]
            raw = q(r, f"SELECT generation_context::text FROM novel_chapter_runs WHERE id='{rid}'")
            ctx = json.loads(raw)
            prod = ctx.get("production") or {}
            print("phase", prod.get("phase"), "accepted", prod.get("accepted"), "calls", prod.get("call_count"))
            for ti, take in enumerate(prod.get("takes") or []):
                vers = take.get("prose_versions") or []
                print(
                    f"take[{ti}] rev={take.get('revisions')} patch={bool(take.get('last_patch'))} "
                    f"err={take.get('patch_error')} lens={[len(v) for v in vers]}"
                )
                if take.get("last_patch"):
                    print("  replacements", len(take["last_patch"].get("replacements") or []))
            return 0 if status == "CANONIZED" else 1
        time.sleep(20)
    print("timeout waiting")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
