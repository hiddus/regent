"""Probe whether a gateway model satisfies the contract `OpenAICompatibleProvider` needs.

`probe_model_gateway_2026_08_11.py` answers "is this alias reachable" with a bare
`ping`. That is not enough to switch the primary model: the agent loop only works
if the model also honours the exact payload shape the provider sends, and a
reasoning model can return HTTP 200 with an empty `content` while spending the
whole `max_tokens` budget on `reasoning_content` — reachable, and still unable to
drive a single turn.

So exercise the four things `provider.chat()` depends on:
  1. `thinking={"type":"disabled"}` is accepted (not 400) and actually suppresses CoT.
  2. a normal turn returns non-empty `content` with `finish_reason=stop`.
  3. `tools` + `tool_choice="auto"` produces a well-formed `tool_calls` envelope.
  4. the tool-result round trip (assistant.tool_calls → role=tool) is accepted.

Runs on the host over SSH so `REGENT_MODEL_API_KEY` never leaves it.

Usage:
  python ops/probe_model_contract_2026_08_11.py                      # configured ids
  python ops/probe_model_contract_2026_08_11.py --models tencent/glm-5.2
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

DEFAULT_MODELS = ("tencent/glm-5.2", "tencent/deepseek-v4-pro", "tencent/deepseek-v4-flash")

REMOTE = r'''
import json, os, urllib.request, urllib.error

MODELS = __MODELS__

def load_secrets(path="/opt/regent/.secrets.env"):
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    except OSError:
        pass
    return out

S = load_secrets()
BASE = (S.get("REGENT_MODEL_BASE_URL") or "https://ai.showmac.com/v1").rstrip("/")
KEY = S.get("REGENT_MODEL_API_KEY") or ""

def post(payload, timeout=120):
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:400]}
    except Exception as e:
        return 0, {"error": type(e).__name__ + ": " + str(e)[:200]}

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write text content to a path in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    },
}

def summarize(status, body):
    if status != 200:
        err = body.get("error") or body
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return {"status": status, "error": str(msg)[:200]}
    ch = (body.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    usage = body.get("usage") or {}
    det = usage.get("completion_tokens_details") or {}
    tc = msg.get("tool_calls") or []
    return {
        "status": 200,
        "finish_reason": ch.get("finish_reason"),
        "content_chars": len(msg.get("content") or ""),
        "reasoning_chars": len(msg.get("reasoning_content") or ""),
        "reasoning_tokens": det.get("reasoning_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "n_tool_calls": len(tc),
        "tool_names": [((t.get("function") or {}).get("name")) for t in tc],
        "tool_args_is_str": [isinstance((t.get("function") or {}).get("arguments"), str) for t in tc],
        "content_head": (msg.get("content") or "")[:80],
    }

ASK = "用一句话说明什么是幂等操作。"

report = {"base": BASE, "key_len": len(KEY), "models": {}}
for model in MODELS:
    r = {}

    # 1) exactly what provider.chat() sends by default: thinking disabled.
    r["thinking_disabled"] = summarize(*post({
        "model": model, "temperature": 0, "max_tokens": 8192,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": ASK}],
    }))

    # 2) control: no thinking field at all — isolates whether the field is the problem.
    r["thinking_omitted"] = summarize(*post({
        "model": model, "temperature": 0, "max_tokens": 8192,
        "messages": [{"role": "user", "content": ASK}],
    }))

    # 3) tool calling, thinking disabled (the agent loop's real shape).
    st, body = post({
        "model": model, "temperature": 0, "max_tokens": 8192,
        "thinking": {"type": "disabled"},
        "tools": [WEATHER_TOOL], "tool_choice": "auto",
        "messages": [
            {"role": "system", "content": "You are a coding agent. Use tools to act."},
            {"role": "user", "content": "把 'hello' 写入 README.md，只调用工具，不要解释。"},
        ],
    })
    r["tools_disabled_thinking"] = summarize(st, body)

    # 4) tool-result round trip — provider replays assistant.tool_calls + role=tool.
    calls = []
    if st == 200:
        calls = ((body.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    if calls:
        call = calls[0]
        args = (call.get("function") or {}).get("arguments")
        assistant = {
            "role": "assistant",
            "content": ((body["choices"][0]["message"]).get("content") or ""),
            "tool_calls": [{
                "id": call.get("id") or "call_0",
                "type": "function",
                "function": {
                    "name": (call.get("function") or {}).get("name"),
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }],
        }
        reasoning = ((body["choices"][0]["message"]).get("reasoning_content") or "")
        if reasoning:
            assistant["reasoning_content"] = reasoning
        r["tool_roundtrip"] = summarize(*post({
            "model": model, "temperature": 0, "max_tokens": 8192,
            "thinking": {"type": "disabled"},
            "tools": [WEATHER_TOOL], "tool_choice": "auto",
            "messages": [
                {"role": "system", "content": "You are a coding agent. Use tools to act."},
                {"role": "user", "content": "把 'hello' 写入 README.md，只调用工具，不要解释。"},
                assistant,
                {"role": "tool", "tool_call_id": assistant["tool_calls"][0]["id"],
                 "name": assistant["tool_calls"][0]["function"]["name"],
                 "content": '{"ok": true, "bytes": 5}'},
            ],
        }))
    else:
        r["tool_roundtrip"] = {"skipped": "no tool_calls in step 3"}

    report["models"][model] = r

print(json.dumps(report, ensure_ascii=False, indent=2))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*", default=list(DEFAULT_MODELS))
    parser.add_argument("--out", default="docs/model-contract-probe-2026-08-11.json")
    args = parser.parse_args()
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing in .env")

    script = REMOTE.replace("__MODELS__", json.dumps(list(args.models)))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        HOST,
        username=CFG.get("LOGIN_USER") or "root",
        password=PASSWORD,
        timeout=40,
        banner_timeout=120,
        auth_timeout=60,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        _, out, err = ssh.exec_command("python3 - <<'PY'\n" + script + "\nPY", timeout=900)
        text = (out.read() + err.read()).decode("utf-8", "replace")
        code = out.channel.recv_exit_status()
    finally:
        ssh.close()

    print(text)
    if args.out:
        target = ROOT / args.out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"[saved] {target}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
