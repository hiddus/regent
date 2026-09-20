"""Debug decision resolve for run6."""
from __future__ import annotations

import json
import secrets
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote
from run_chapter1 import _api

WORK = "ca565eb3-4cb6-4e47-be69-410afa00cdb3"
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"
r = Remote()

sql_subject = (
    "SELECT p.subject FROM novel_principals p "
    f"JOIN novel_works w ON w.owner_id=p.id WHERE w.id='{WORK}'"
)
subject = r.run(f"{PSQL} {shlex.quote(sql_subject)}", timeout=60).out.strip()
print("subject", subject)

auth = _api(r, "POST", "/auth/session", params=f"subject={subject}&display_name=R21")
token = (auth.get("body") or {}).get("token")
print("auth", auth.get("status"), bool(token))

decs = _api(r, "GET", f"/works/{WORK}/decisions", token=token)
print("list_status", decs.get("status"))
print("list_body", json.dumps(decs.get("body"), ensure_ascii=False, indent=2)[:2500])

sql_row = (
    "SELECT id::text, state, default_option_id, confirm_nonce, version "
    f"FROM novel_decision_requests WHERE work_id='{WORK}'"
)
print("DB", r.run(f"{PSQL} {shlex.quote(sql_row)}", timeout=60).out)

items = decs.get("body") or []
if not items:
    sys.exit(1)
d = items[0]
body = {
    "option_id": d.get("default_option_id"),
    "accept_default": True,
    "confirm_nonce": d.get("confirm_nonce"),
    "client_nonce": f"dbg-{secrets.token_hex(4)}",
}
print("post_body", body)
res = _api(
    r,
    "POST",
    f"/works/{WORK}/decisions/{d['decision_id']}/resolve",
    token=token,
    body=body,
    timeout=120,
)
print("resolve", json.dumps(res, ensure_ascii=False)[:2000])
