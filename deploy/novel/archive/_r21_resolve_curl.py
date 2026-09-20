"""Resolve decision via docker exec curl-like python with full error."""
from __future__ import annotations

import json
import shlex
import sys
import uuid

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

WORK = "ca565eb3-4cb6-4e47-be69-410afa00cdb3"
DEC = "c6dc32b3-86cc-4e8d-bd75-7761dd6288b2"
NONCE = "48bd636ff5d987b68adb3d669e657df1"

r = Remote()
# auth inside container
auth_script = f"""
import urllib.request, json
url='http://localhost:8000/v1/novel/auth/session?subject=ch1-afa16b2c&display_name=R21'
req=urllib.request.Request(url, method='POST')
with urllib.request.urlopen(req, timeout=60) as resp:
    print(resp.read().decode())
"""
r.write_text("/tmp/_auth.py", auth_script)
r.run("docker cp /tmp/_auth.py regent-api:/tmp/_auth.py", timeout=15)
auth = json.loads(r.run("docker exec regent-api python /tmp/_auth.py", timeout=60).out.strip())
token = auth["token"]
print("token_ok", bool(token))

body = {
    "option_id": "A",
    "accept_default": True,
    "confirm_nonce": NONCE,
    "client_nonce": str(uuid.uuid4()),
}
resolve_script = f"""
import urllib.request, urllib.error, json
url='http://localhost:8000/v1/novel/works/{WORK}/decisions/{DEC}/resolve'
body={json.dumps(body)!r}
token={token!r}
req=urllib.request.Request(url, method='POST', data=json.dumps(json.loads(body) if False else {json.dumps(body)}).encode())
req.add_header('Content-Type','application/json')
req.add_header('Authorization','Bearer '+token)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        print('OK', resp.status, resp.read().decode()[:800])
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read().decode()[:800])
except Exception as e:
    print('ERR', type(e).__name__, str(e)[:400])
"""
# simpler write
resolve_script = (
    "import urllib.request, urllib.error, json\n"
    f"url='http://localhost:8000/v1/novel/works/{WORK}/decisions/{DEC}/resolve'\n"
    f"payload={json.dumps(body, ensure_ascii=False)!r}\n"
    f"token={token!r}\n"
    "data=payload.encode('utf-8')\n"
    "req=urllib.request.Request(url, method='POST', data=data)\n"
    "req.add_header('Content-Type','application/json')\n"
    "req.add_header('Authorization','Bearer '+token)\n"
    "try:\n"
    "    with urllib.request.urlopen(req, timeout=120) as resp:\n"
    "        print('OK', resp.status, resp.read().decode()[:1200])\n"
    "except urllib.error.HTTPError as e:\n"
    "    print('HTTP', e.code, e.read().decode()[:1200])\n"
    "except Exception as e:\n"
    "    print('ERR', type(e).__name__, str(e)[:400])\n"
)
r.write_text("/tmp/_resolve.py", resolve_script)
r.run("docker cp /tmp/_resolve.py regent-api:/tmp/_resolve.py", timeout=15)
print(r.run("docker exec regent-api python /tmp/_resolve.py", timeout=180).out)
