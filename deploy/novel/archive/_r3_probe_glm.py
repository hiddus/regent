import json
import os
import urllib.request

base = os.environ["REGENT_MODEL_BASE_URL"].rstrip("/")
key = os.environ["REGENT_MODEL_API_KEY"]
model = os.environ["REGENT_MODEL_NAME_2"]


def call(thinking):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": 'Reply with exactly: {"ok":true}'}],
        "max_tokens": 128,
        "temperature": 0,
    }
    if thinking is not None:
        body["thinking"] = thinking
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode())
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        return {
            "thinking": thinking,
            "content": msg.get("content"),
            "reasoning": (msg.get("reasoning_content") or "")[:120],
            "usage": data.get("usage"),
        }
    except Exception as e:
        raw = getattr(e, "read", lambda: b"")()
        return {"thinking": thinking, "error": str(e)[:120], "body": raw.decode(errors="replace")[:300]}


for t in (
    None,
    {"type": "enabled"},
    {"type": "low"},
    {"type": "high"},
    {"type": "max"},
    "low",
    "high",
):
    print(json.dumps(call(t), ensure_ascii=False))
