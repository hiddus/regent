"""从容器内直连模型端点，确认"调用失败"到底是端点问题还是我们的问题。

只读（一次 16 token 的 ping）。用法：python deploy/novel/d4_probe_model.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

PROBE = r'''
import json
import os
import urllib.error
import urllib.request

base = os.environ.get("REGENT_MODEL_BASE_URL", "")
key = os.environ.get("REGENT_MODEL_API_KEY", "")
model = os.environ.get("REGENT_MODEL_NAME", "")
print("base =", base)
print("model =", model)
print("api_key set =", bool(key))

payload = {
    "model": model,
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 16,
}
req = urllib.request.Request(
    base.rstrip("/") + "/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        print("HTTP", resp.status)
        print(resp.read().decode()[:500])
except urllib.error.HTTPError as exc:
    print("HTTPError", exc.code)
    print(exc.read().decode()[:500])
except Exception as exc:  # noqa: BLE001
    print("ERROR", type(exc).__name__, str(exc)[:300])
'''

CONTAINERS = ("regent-api", "regent-worker-2")


def main() -> int:
    r = Remote()
    r.write_text("/tmp/_probe_model.py", PROBE)
    for name in CONTAINERS:
        print(f"\n===== {name} =====")
        r.run(f"docker cp /tmp/_probe_model.py {name}:/tmp/_probe_model.py")
        out = r.run(f"docker exec {name} python /tmp/_probe_model.py", timeout=180).out
        print(out.strip() or "(无输出)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
