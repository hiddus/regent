"""Probe new primary model via showmac gateway from inside regent-api."""
from __future__ import annotations

import json
import os
import urllib.request

base = os.environ["REGENT_MODEL_BASE_URL"].rstrip("/")
key = os.environ["REGENT_MODEL_API_KEY"]
model = os.environ["REGENT_MODEL_NAME"]
url = f"{base}/chat/completions"
body = {
    "model": model,
    "messages": [{"role": "user", "content": "只回复一个字：好"}],
    "max_tokens": 16,
    "temperature": 0,
}
req = urllib.request.Request(
    url,
    data=json.dumps(body).encode(),
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
        choice = (data.get("choices") or [{}])[0]
        msg = (choice.get("message") or {}).get("content")
        print(json.dumps({"ok": True, "model": model, "content": msg, "usage": data.get("usage")}, ensure_ascii=False))
except Exception as e:
    err = getattr(e, "read", lambda: b"")()
    print(json.dumps({"ok": False, "model": model, "error": str(e)[:300], "body": err.decode(errors="replace")[:500]}, ensure_ascii=False))
