"""Poll an existing F4 chapter work to terminal and dump patch evidence."""
from __future__ import annotations

import json
import shlex
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote
from run_chapter1 import _api, dump_diagnostics

WORK = sys.argv[1]
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 2400
PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


def main() -> int:
    r = Remote()
    subject = q(
        r,
        "SELECT p.subject FROM novel_principals p JOIN novel_works w ON w.owner_id=p.id "
        f"WHERE w.id='{WORK}'",
    )
    auth = _api(r, "POST", "/auth/session", params=f"subject={subject}&display_name=R21F4")
    token = auth.get("body", {}).get("token", "")
    if not token:
        print("no token", auth)
        return 1
    deadline = time.time() + LIMIT
    last = ""
    while time.time() < deadline:
        time.sleep(20)
        prog = _api(r, "GET", f"/works/{WORK}/runs", token=token).get("body", {})
        state = prog.get("state", "?")
        step = prog.get("current_step", "")
        tag = f"{state}/{step}"
        if tag != last:
            print(time.strftime("%H:%M:%S"), tag)
            last = tag
        if state == "PENDING_DECISION":
            # reuse fixed path from run_chapter1 by spawning resolve2 helper
            from subprocess import run as sprun
            import os

            sprun(
                [
                    sys.executable,
                    os.path.join(os.path.dirname(__file__), "_r21_f4_resolve2.py"),
                    WORK,
                ],
                check=False,
            )
            continue
        if state == "CANONIZED":
            print("[OK] CANONIZED")
            break
        if state in ("TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
            print("[FAIL]", state)
            dump_diagnostics(r, WORK)
            break
    else:
        print("[FAIL] timeout", last)
        dump_diagnostics(r, WORK)
        return 1

    # evidence dump
    from subprocess import run as sprun
    import os

    sprun(
        [
            sys.executable,
            "-u",
            os.path.join(os.path.dirname(__file__), "_r21_f4_evidence.py"),
            WORK,
        ],
        check=False,
    )
    return 0 if state == "CANONIZED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
