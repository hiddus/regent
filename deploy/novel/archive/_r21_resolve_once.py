from __future__ import annotations

import json
import uuid
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote
from run_chapter1 import _api

WORK = "ca565eb3-4cb6-4e47-be69-410afa00cdb3"
r = Remote()
tok = _api(r, "POST", "/auth/session", params="subject=ch1-afa16b2c&display_name=R21")["body"]["token"]
decs = _api(r, "GET", f"/works/{WORK}/decisions", token=tok)
print("n", len(decs.get("body") or []))
d = (decs.get("body") or [None])[0]
print("decision", json.dumps(d, ensure_ascii=False)[:600] if d else None)
if not d:
    raise SystemExit(1)
res = _api(
    r,
    "POST",
    f"/works/{WORK}/decisions/{d['decision_id']}/resolve",
    token=tok,
    body={
        "option_id": d.get("default_option_id"),
        "accept_default": True,
        "confirm_nonce": d.get("confirm_nonce"),
        "client_nonce": str(uuid.uuid4()),
    },
    timeout=120,
)
print("resolve", json.dumps(res, ensure_ascii=False)[:1000])
prog = _api(r, "GET", f"/works/{WORK}/runs", token=tok)
print("prog", (prog.get("body") or {}).get("state"), (prog.get("body") or {}).get("current_step"))
