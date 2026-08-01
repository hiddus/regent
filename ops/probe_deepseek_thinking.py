"""S0 probe: DeepSeek thinking on vs disabled vs empty length signature."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}

REMOTE = r'''
import json
from regent.config import get_settings
from regent.model import OpenAICompatibleProvider
from regent.model.chat import ChatMessage, ToolSpec
import asyncio

settings = get_settings()
key = settings.model_api_key.get_secret_value() if settings.model_api_key else ""
base = settings.model_base_url or "https://api.deepseek.com"
model = settings.model_name or "deepseek-v4-flash"
tools = [ToolSpec(name="write_file", description="Write a file", parameters={
    "type": "object",
    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path", "content"],
})]
prompt = "Create a minimal Flask hello app in src/app.py using the write_file tool. Do not explain."

async def one(mode: str, max_tokens: int = 8192):
    provider = OpenAICompatibleProvider(
        base_url=base, api_key=key, model=model,
        max_output_tokens=max_tokens, thinking_mode=mode, max_http_retries=0,
        timeout_seconds=90,
    )
    try:
        resp = await provider.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            tools=tools, temperature=0,
        )
        return {
            "mode": mode, "ok": True,
            "finish_reason": resp.finish_reason,
            "content_chars": len(resp.message.content or ""),
            "reasoning_chars": len(resp.message.reasoning_content or ""),
            "tool_calls": [c.name for c in resp.message.tool_calls],
            "usage": {
                "prompt": resp.usage.input_tokens,
                "completion": resp.usage.output_tokens,
                "reasoning": resp.usage.reasoning_tokens,
            },
            "diag": provider.last_chat_diagnostics,
        }
    except Exception as exc:
        return {
            "mode": mode, "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "diag": getattr(provider, "last_chat_diagnostics", {}),
        }

async def main():
    rows = []
    rows.append(await one("default", 8192))
    rows.append(await one("disabled", 8192))
    print(json.dumps({"model": model, "base_url": base, "rows": rows}, ensure_ascii=False))

asyncio.run(main())
'''


def main() -> int:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/_probe_thinking.py", "w") as f:
        f.write(REMOTE)
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/_probe_thinking.py regent-api:/tmp/_probe_thinking.py "
        "&& docker exec -w /tmp regent-api python _probe_thinking.py",
        timeout=300,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
