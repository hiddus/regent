"""用应用自己的 provider 打一次真实结构化调用，看结构化路径到底失败在哪。

与 d4_probe_model.py 的区别：那个只证明"端点通"，这个证明"我们的调用方式通"。
只读一次极小请求。用法：python deploy/novel/d4_probe_structured.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

PROBE = r'''
import asyncio
import json
import os

from pydantic import BaseModel

from regent.model.factory import build_model_provider
from regent.config import get_settings


class Ping(BaseModel):
    ok: bool
    note: str


async def main() -> None:
    settings = get_settings()
    provider = build_model_provider(settings)
    print("timeout_seconds =", getattr(provider, "timeout_seconds", "?"))
    print("max_output_tokens =", getattr(provider, "max_output_tokens", "?"))
    try:
        resp = await provider.generate_structured(
            system_prompt="Reply in JSON only.",
            user_prompt='Return {"ok": true, "note": "pong"}',
            response_model=Ping,
            temperature=0,
        )
        print("OK output =", resp.output.model_dump())
        print("model =", resp.model, "usage =", resp.usage)
    except Exception as exc:  # noqa: BLE001
        print("FAILED", type(exc).__name__, str(exc)[:600])
        raw = getattr(exc, "raw", None)
        if raw:
            print("raw =", json.dumps(raw)[:600])


asyncio.run(main())
'''

CONTAINERS = ("regent-api",)


def main() -> int:
    r = Remote()
    r.write_text("/tmp/_probe_struct.py", PROBE)
    for name in CONTAINERS:
        print(f"\n===== {name} =====")
        r.run(f"docker cp /tmp/_probe_struct.py {name}:/tmp/_probe_struct.py")
        out = r.run(
            f"docker exec {name} python /tmp/_probe_struct.py", timeout=300
        ).out
        print(out.strip() or "(无输出)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
