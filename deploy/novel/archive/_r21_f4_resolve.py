"""Resolve pending decisions for a work via in-container urllib (reliable)."""
from __future__ import annotations

import json
import secrets
import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote  # noqa: E402

PSQL = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()


def main() -> int:
    work_id = sys.argv[1]
    r = Remote()
    subject = q(
        r,
        "SELECT p.subject FROM novel_principals p "
        "JOIN novel_works w ON w.owner_id=p.id "
        f"WHERE w.id='{work_id}'",
    )
    print("subject", subject)
    rows = q(
        r,
        "SELECT id::text, state, COALESCE(default_option_id,''), "
        "COALESCE(confirm_nonce,'') FROM novel_decisions "
        f"WHERE work_id='{work_id}' AND upper(state)='PENDING' "
        "ORDER BY created_at",
    )
    print("pending_rows", rows or "(none)")
    if not rows:
        return 0

    auth_script = (
        "import urllib.request, json\n"
        f"url='http://localhost:8000/v1/novel/auth/session?subject={subject}&display_name=R21F4'\n"
        "req=urllib.request.Request(url, method='POST')\n"
        "with urllib.request.urlopen(req, timeout=60) as resp:\n"
        "    print(resp.read().decode())\n"
    )
    r.write_text("/tmp/_f4_auth.py", auth_script)
    r.run("docker cp /tmp/_f4_auth.py regent-api:/tmp/_f4_auth.py", timeout=15)
    auth = json.loads(
        r.run("docker exec regent-api python /tmp/_f4_auth.py", timeout=60).out.strip()
    )
    token = auth["token"]

    for line in rows.splitlines():
        dec_id, _state, opt, nonce = line.split("|", 3)
        body = {
            "option_id": opt or None,
            "accept_default": True,
            "confirm_nonce": nonce,
            "client_nonce": f"f4-{secrets.token_hex(4)}",
        }
        script = (
            "import urllib.request, urllib.error, json\n"
            f"url='http://localhost:8000/v1/novel/works/{work_id}/decisions/{dec_id}/resolve'\n"
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
        print("dec", dec_id, r.run("docker exec regent-api python /tmp/_f4_resolve.py", timeout=180).out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
