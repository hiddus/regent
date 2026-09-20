import json
import os
import urllib.request

base = os.environ["REGENT_MODEL_BASE_URL"].rstrip("/")
key = os.environ["REGENT_MODEL_API_KEY"]


def call(model: str, *, thinking=None, max_tokens=128):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": 'Reply with exactly: {"ok":true}'}],
        "max_tokens": max_tokens,
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
    except Exception as e:
        raw = getattr(e, "read", lambda: b"")()
        return {"error": str(e)[:200], "body": raw.decode(errors="replace")[:400]}
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    return {
        "model": model,
        "thinking": thinking,
        "content": msg.get("content"),
        "reasoning_content": (msg.get("reasoning_content") or "")[:200],
        "usage": data.get("usage"),
        "finish": ((data.get("choices") or [{}])[0].get("finish_reason")),
    }


primary = os.environ["REGENT_MODEL_NAME"]
secondary = os.environ.get("REGENT_MODEL_NAME_2") or ""
print(json.dumps(call(primary, thinking={"type": "disabled"}), ensure_ascii=False))
print(json.dumps(call(primary, thinking={"type": "enabled"}, max_tokens=256), ensure_ascii=False))
print(json.dumps(call(primary, thinking=None, max_tokens=256), ensure_ascii=False))
if secondary:
    print(json.dumps(call(secondary, thinking={"type": "disabled"}), ensure_ascii=False))
