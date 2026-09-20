from __future__ import annotations

import json
import secrets
import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
WORK = sys.argv[1] if len(sys.argv) > 1 else "d814b81f-9a8a-485e-afa3-7778a1d05fa7"


def q(r, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


r = Remote()
print("cols:")
print(
    q(
        r,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='novel_decision_requests' ORDER BY ordinal_position",
    )
)
print("rows:")
print(
    q(
        r,
        "SELECT id::text, state, COALESCE(default_option_id,''), "
        "COALESCE(confirm_nonce,''), created_at::text "
        f"FROM novel_decision_requests WHERE work_id='{WORK}' "
        "ORDER BY created_at DESC LIMIT 5",
    )
)
subject = q(
    r,
    "SELECT p.subject FROM novel_principals p JOIN novel_works w ON w.owner_id=p.id "
    f"WHERE w.id='{WORK}'",
)
print("subject", subject)
rows = q(
    r,
    "SELECT id::text, COALESCE(default_option_id,''), COALESCE(confirm_nonce,'') "
    f"FROM novel_decision_requests WHERE work_id='{WORK}' AND upper(state)='PENDING' "
    "ORDER BY created_at",
)
if not rows:
    print("no pending")
    raise SystemExit(0)

auth_script = (
    "import urllib.request, json\n"
    f"url='http://localhost:8000/v1/novel/auth/session?subject={subject}&display_name=R21F4'\n"
    "req=urllib.request.Request(url, method='POST')\n"
    "with urllib.request.urlopen(req, timeout=60) as resp:\n"
    "    print(resp.read().decode())\n"
)
r.write_text("/tmp/_f4_auth.py", auth_script)
r.run("docker cp /tmp/_f4_auth.py regent-api:/tmp/_f4_auth.py", timeout=15)
auth = json.loads(r.run("docker exec regent-api python /tmp/_f4_auth.py", timeout=60).out.strip())
token = auth["token"]

for line in rows.splitlines():
    dec_id, opt, nonce = line.split("|", 2)
    body = {
        "option_id": opt or None,
        "accept_default": True,
        "confirm_nonce": nonce,
        "client_nonce": f"f4-{secrets.token_hex(4)}",
    }
    script = (
        "import urllib.request, urllib.error, json\n"
        f"url='http://localhost:8000/v1/novel/works/{WORK}/decisions/{dec_id}/resolve'\n"
        f"payload={json.dumps(body, ensure_ascii=False)!r}\n"
        f"token={token!r}\n"
        "req=urllib.request.Request(url, method='POST', data=payload.encode('utf-8'))\n"
        "req.add_header('Content-Type','application/json')\n"
        "req.add_header('Authorization','Bearer '+token)\n"
        "try:\n"
        "    with urllib.request.urlopen(req, timeout=120) as resp:\n"
        "        print('OK', resp.status, resp.read().decode()[:800])\n"
        "except urllib.error.HTTPError as e:\n"
        "    print('HTTP', e.code, e.read().decode()[:800])\n"
        "except Exception as e:\n"
        "    print('ERR', type(e).__name__, str(e)[:400])\n"
    )
    r.write_text("/tmp/_f4_resolve.py", script)
    r.run("docker cp /tmp/_f4_resolve.py regent-api:/tmp/_f4_resolve.py", timeout=15)
    print("resolve", dec_id, r.run("docker exec regent-api python /tmp/_f4_resolve.py", timeout=180).out)
