"""Switch the production primary model off tencent/deepseek-v4-flash.

flash returns 402 (free trial quota exhausted, postpaid billing off) for every
request, so the whole worker fleet is dead in the water. The gateway probe in
docs/model-contract-probe-2026-08-11.json shows the two remaining ids answer
200 for plain chat, thinking-disabled chat and tool round-trips:

  tencent/deepseek-v4-pro  -> primary
  tencent/glm-5.2          -> secondary

The on-disk env files also still carry the non-canonical aliases
(``DeepSeek-v4-flash`` / ``GLM5.2``) while the live containers run the canonical
``tencent/...`` ids, so any future recreate from files would reintroduce a
domain/name mismatch. This writes canonical ids into every file the containers
read, drops REGENT_MODEL_NAME_3 entirely, and recreates api + all workers
(docker cannot change env in place).

Usage:
  python ops/switch_primary_model_2026_08_11.py            # dry-run
  python ops/switch_primary_model_2026_08_11.py --execute
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
USER = CFG.get("LOGIN_USER") or "root"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

PRIMARY = "tencent/deepseek-v4-pro"
SECONDARY = "tencent/glm-5.2"

REMOTE = r'''
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

EXECUTE = __EXECUTE__
SET_VALUES = {
    "REGENT_MODEL_NAME": __PRIMARY__,
    "REGENT_MODEL_NAME_2": __SECONDARY__,
}
# Only two ids are usable; a third slot can only go stale (or point back at flash).
DROP_KEYS = ("REGENT_MODEL_NAME_3",)
ENV_FILES = (
    Path("/opt/regent/.secrets.env"),
    Path("/opt/regent/.env"),
    Path("/opt/regent/.runtime.env"),
    Path("/opt/regent/.deploy.env"),
)


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        out[k.strip()] = v
    return out


def rewrite_env(path: Path) -> None:
    """Update keys in place, delete DROP_KEYS lines, keep everything else byte-stable."""
    if not path.is_file():
        print("skip_missing", path)
        return
    mode = path.stat().st_mode & 0o777
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in DROP_KEYS:
                continue
            if key in SET_VALUES:
                out.append(f"{key}={SET_VALUES[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in SET_VALUES.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, mode or 0o600)
    except OSError:
        pass
    print("updated", path)


before = {
    str(p): {
        k: v
        for k, v in load_env(p).items()
        if k.startswith("REGENT_MODEL_NAME") or k == "REGENT_MODEL_BASE_URL"
    }
    for p in ENV_FILES
}
print(json.dumps({"before_files": before}, ensure_ascii=False))


def live_env(container: str) -> dict[str, str]:
    keys = ("REGENT_MODEL_BASE_URL", "REGENT_MODEL_NAME", "REGENT_MODEL_NAME_2", "REGENT_MODEL_NAME_3")
    out: dict[str, str] = {}
    for key in keys:
        try:
            out[key] = subprocess.check_output(
                ["docker", "exec", container, "printenv", key],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            out[key] = ""
    return out


def container_names() -> list[str]:
    names = subprocess.check_output(
        ["docker", "ps", "-a", "--format", "{{.Names}}"], text=True
    ).splitlines()
    api = [n for n in names if n == "regent-api"]
    workers = sorted(n for n in names if n == "regent-worker" or n.startswith("regent-worker-"))
    return api + workers


targets = container_names()
print(json.dumps({"before_live": live_env("regent-worker"), "targets": targets}, ensure_ascii=False))

if not EXECUTE:
    print(json.dumps({"dry_run": True, "would_set": SET_VALUES, "would_drop": list(DROP_KEYS)}, ensure_ascii=False))
    raise SystemExit(0)

for path in ENV_FILES:
    rewrite_env(path)

DOCKER_GID = "0"
try:
    import grp

    DOCKER_GID = str(grp.getgrnam("docker").gr_gid)
except Exception:
    DOCKER_GID = str(os.stat("/var/run/docker.sock").st_gid)

file_env: dict[str, str] = {}
for path in (
    Path("/opt/regent/.runtime.env"),
    Path("/opt/regent/.deploy.env"),
    Path("/opt/regent/.secrets.env"),
    Path("/opt/regent/.env"),
):
    file_env.update(load_env(path))


def inspect(name: str) -> dict:
    return json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]


def recreate(name: str, *, add_docker_group: bool) -> None:
    info = inspect(name)
    cfg = info["Config"]
    host = info["HostConfig"]
    env: dict[str, str] = {}
    for item in cfg.get("Env") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    env.update(file_env)
    env.update(SET_VALUES)
    for key in DROP_KEYS:
        env.pop(key, None)

    binds = list(host.get("Binds") or [])
    if "worker" in name:
        for need in (
            "/var/run/docker.sock:/var/run/docker.sock",
            "/usr/bin/docker:/usr/bin/docker:ro",
        ):
            if not any(need.split(":")[0] in b for b in binds):
                binds.append(need)
    # Durable console: keep the host tree bind so recreate cannot roll the UI back.
    if name == "regent-api":
        host_console = Path("/opt/regent/console-dist")
        api_console = "/app/apps/regent-console/dist"
        if (host_console / "index.html").is_file():
            binds = [
                b
                for b in binds
                if not (
                    len(b.split(":")) > 1
                    and b.split(":")[1].rstrip("/") == api_console.rstrip("/")
                )
            ]
            binds.append(f"{host_console}:{api_console}:ro")
        else:
            print("WARN: missing /opt/regent/console-dist; api will use image console")

    subprocess.check_call(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL)
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        host.get("NetworkMode") or "regent-net",
        "--restart",
        "unless-stopped",
    ]
    if add_docker_group:
        cmd += ["--group-add", DOCKER_GID]
    for b in binds:
        cmd += ["-v", b]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    for port, hosts in (host.get("PortBindings") or {}).items():
        if hosts and hosts[0].get("HostPort"):
            cmd += ["-p", f"{hosts[0]['HostPort']}:{port.split('/')[0]}"]
    if cfg.get("User"):
        cmd += ["--user", cfg["User"]]
    if cfg.get("WorkingDir"):
        cmd += ["-w", cfg["WorkingDir"]]
    cmd.append(cfg["Image"])
    if cfg.get("Cmd"):
        cmd += list(cfg["Cmd"])
    print("recreate", name)
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL)


for name in targets:
    recreate(name, add_docker_group=("worker" in name))

print(json.dumps({"recreated": targets}, ensure_ascii=False))
'''

VERIFY = r'''
import json
import os

import httpx
from regent.config import get_settings

s = get_settings()
key = s.model_api_key.get_secret_value() if s.model_api_key else ""
base = (s.model_base_url or "").rstrip("/")
# The deployed image predates model_name_2/3 in Settings; env is the source of truth.
secondary = os.environ.get("REGENT_MODEL_NAME_2") or ""
report = {
    "live_base_url": base,
    "live_primary": s.model_name,
    "live_secondary": secondary,
    "live_tertiary": os.environ.get("REGENT_MODEL_NAME_3", "<unset>"),
    "thinking_mode": getattr(s, "model_thinking_mode", "<not in deployed image>"),
    "key_len": len(key),
    "max_output_tokens": getattr(s, "model_max_output_tokens", "<not in deployed image>"),
    "timeout_seconds": getattr(s, "model_timeout_seconds", None),
    "probes": {},
}
headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
for model in [m for m in (s.model_name, secondary) if m]:
    try:
        r = httpx.post(
            base + "/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 64,
            },
            timeout=60.0,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        choice = (body.get("choices") or [{}])[0]
        report["probes"][model] = {
            "status": r.status_code,
            "finish_reason": choice.get("finish_reason"),
            "content": (choice.get("message") or {}).get("content", "")[:60],
            "error": (body.get("error") or {}).get("message", "")[:160] if r.status_code != 200 else "",
        }
    except Exception as exc:  # pragma: no cover - ops probe
        report["probes"][model] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

# Real code path: provider adapter + tool calling, exactly what AgentRunner drives.
try:
    import asyncio

    from regent.model.chat import ChatMessage, ToolSpec
    from regent.model.factory import build_model_provider

    tool = ToolSpec(
        name="write_file",
        description="Write text to a workspace file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    )
    provider = build_model_provider(s)
    history = [
        ChatMessage(role="system", content="Use tools when asked to change files."),
        ChatMessage(role="user", content="Create hello.txt containing hi."),
    ]

    async def two_turn():
        first = await provider.chat(messages=history, tools=[tool])
        if not first.message.tool_calls:
            return first, None
        call = first.message.tool_calls[0]
        followup = [
            *history,
            first.message,
            ChatMessage(role="tool", content="ok", tool_call_id=call.id, name=call.name),
            ChatMessage(role="user", content="Reply DONE."),
        ]
        return first, await provider.chat(messages=followup, tools=[tool])

    first, second = asyncio.run(two_turn())
    report["provider_tool_path"] = {
        "model": first.model,
        "turn1_finish_reason": first.finish_reason,
        "turn1_tool_names": [c.name for c in first.message.tool_calls],
        "turn1_args_parsed": [isinstance(c.arguments, dict) for c in first.message.tool_calls],
        "turn1_output_tokens": first.usage.output_tokens,
        "turn1_reasoning_tokens": getattr(first.usage, "reasoning_tokens", "<not in image>"),
        "turn2_finish_reason": second.finish_reason if second else "<no tool call in turn 1>",
        "turn2_content": (second.message.content or "")[:60] if second else "",
    }
except Exception as exc:  # pragma: no cover - ops probe
    report["provider_tool_path"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}

print(json.dumps(report, ensure_ascii=False, indent=2))
'''


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[str, int]:
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    text = (out.read() + err.read()).decode("utf-8", "replace")
    return text, out.channel.recv_exit_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="probe the live runtime without touching env files or containers",
    )
    args = parser.parse_args()
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing in .env")

    remote = (
        REMOTE.replace("__EXECUTE__", "True" if args.execute else "False")
        .replace("__PRIMARY__", json.dumps(PRIMARY))
        .replace("__SECONDARY__", json.dumps(SECONDARY))
    )
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        HOST,
        username=USER,
        password=PASSWORD,
        timeout=40,
        banner_timeout=120,
        auth_timeout=60,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        if not args.verify_only:
            out, code = run(ssh, "python3 - <<'PY'\n" + remote + "\nPY", timeout=420)
            print(out)
            if code != 0 or not args.execute:
                return code
            time.sleep(12)

        out, _ = run(
            ssh,
            "docker exec -i regent-worker python - <<'PY'\n" + VERIFY + "\nPY",
            timeout=180,
        )
        print("=== LIVE VERIFY ===")
        print(out)
        out, _ = run(
            ssh,
            "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'regent-(api|worker)'; "
            "curl -sS -m 20 http://127.0.0.1:8000/health/ready | head -c 400; echo",
        )
        print(out)
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
