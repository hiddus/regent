"""Resume run6: accept default decision and poll to terminal."""
from __future__ import annotations

import json
import secrets
import shlex
import sys
import time
import uuid

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote
from run_chapter1 import _api, dump_diagnostics  # noqa: E402

WORK = "ca565eb3-4cb6-4e47-be69-410afa00cdb3"
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
    with Remote() as r:
        subject = q(
            r,
            f"SELECT p.subject FROM novel_principals p "
            f"JOIN novel_works w ON w.owner_id = p.id "
            f"WHERE w.id = '{WORK}'",
        )
        print("subject", subject)
        auth = _api(r, "POST", "/auth/session", params=f"subject={subject}&display_name=R21")
        token = auth.get("body", {}).get("token", "")
        if not token:
            print("no token", auth)
            return 1

        decs = _api(r, "GET", f"/works/{WORK}/decisions", token=token)
        print("decisions HTTP", decs.get("status"), "n", len(decs.get("body") or []))
        for d in decs.get("body") or []:
            print(" ", d.get("decision_id"), d.get("status"), d.get("default_option_id"))
            if d.get("status") in (None, "PENDING", "OPEN", "awaiting", "AWAITING"):
                # try common pending statuses
                pass
            status = str(d.get("status") or "").upper()
            if status in ("PENDING", "OPEN", "AWAITING", "AWAITING_USER", ""):
                body = {
                    "option_id": None,
                    "accept_default": True,
                    "confirm_nonce": d.get("confirm_nonce"),
                    "client_nonce": f"r21-{secrets.token_hex(4)}",
                }
                res = _api(
                    r,
                    "POST",
                    f"/works/{WORK}/decisions/{d['decision_id']}/resolve",
                    token=token,
                    body=body,
                )
                print(" resolve", res.get("status"), json.dumps(res.get("body"), ensure_ascii=False)[:200])

        # also resolve any that look unresolved
        for d in decs.get("body") or []:
            if d.get("resolved_option_id") or d.get("resolved_at"):
                continue
            body = {
                "option_id": d.get("default_option_id"),
                "accept_default": True,
                "confirm_nonce": d.get("confirm_nonce"),
                "client_nonce": str(uuid.uuid4()),
            }
            res = _api(
                r,
                "POST",
                f"/works/{WORK}/decisions/{d['decision_id']}/resolve",
                token=token,
                body=body,
            )
            print(" resolve2", res.get("status"), json.dumps(res.get("body"), ensure_ascii=False)[:300])

        deadline = time.time() + limit
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
                # keep accepting defaults
                decs = _api(r, "GET", f"/works/{WORK}/decisions", token=token)
                for d in decs.get("body") or []:
                    if d.get("resolved_at") or d.get("resolved_option_id"):
                        continue
                    _api(
                        r,
                        "POST",
                        f"/works/{WORK}/decisions/{d['decision_id']}/resolve",
                        token=token,
                        body={
                            "option_id": d.get("default_option_id"),
                            "accept_default": True,
                            "confirm_nonce": d.get("confirm_nonce"),
                            "client_nonce": str(uuid.uuid4()),
                        },
                    )
                    print(" auto-resolved", d.get("decision_id"))
            if state == "CANONIZED":
                print("[OK] CANONIZED")
                return 0
            if state in ("TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"):
                print("[FAIL]", state)
                dump_diagnostics(r, WORK)
                return 1
        print("[FAIL] timeout", last)
        dump_diagnostics(r, WORK)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
