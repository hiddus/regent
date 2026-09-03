"""Restore the hot-patched production code that the 2026-08-11 container recreate wiped.

Production is deployed by ``sftp -> docker cp`` per-file patches into
site-packages, so ``docker rm -f`` + ``docker run`` rolled every container back to
the 2026-07-27 image (125 modules missing, 78 stale). ``/opt/regent/current`` is
the on-host release tree the patches came from and it matches the DB head
(20260802_0043), so it is the correct restore source -- unlike the local repo,
which additionally carries 0044-0047 migrations that production has not applied.

Dry-run first: it reports how fresh the release tree is and how it compares to
the local working tree (content compared with line endings normalised), so we can
see whether restoring from the host would silently drop newer intended code.

Usage:
  python ops/restore_container_code_2026_08_11.py            # dry-run report
  python ops/restore_container_code_2026_08_11.py --execute  # restore + verify
"""

from __future__ import annotations

import argparse
import hashlib
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
USER = CFG.get("LOGIN_USER") or "root"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

LOCAL_PKG = ROOT / "core" / "src" / "regent"
SUFFIXES = (".py", ".json", ".md", ".txt")


def local_manifest() -> dict[str, str]:
    """md5 of every packaged file, newlines normalised so CRLF checkouts compare."""
    out: dict[str, str] = {}
    for path in LOCAL_PKG.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if "__pycache__" in path.parts:
            continue
        blob = path.read_bytes().replace(b"\r\n", b"\n")
        out[path.relative_to(LOCAL_PKG).as_posix()] = hashlib.md5(blob).hexdigest()
    return out


COMPARE = r'''
import hashlib, json, os, subprocess

LOCAL = json.loads(r"""__LOCAL_JSON__""")
rel = "/opt/regent/current/core/src/regent"
SUFFIXES = (".py", ".json", ".md", ".txt")


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read().replace(b"\r\n", b"\n")).hexdigest()


release = {}
newest = ""
for root, _dirs, files in os.walk(rel):
    if "__pycache__" in root:
        continue
    for name in files:
        if not name.endswith(SUFFIXES):
            continue
        src = os.path.join(root, name)
        key = os.path.relpath(src, rel).replace(os.sep, "/")
        release[key] = digest(src)
        stamp = os.path.getmtime(src)
        newest = max(newest, str(stamp))

only_local = sorted(set(LOCAL) - set(release))
only_release = sorted(set(release) - set(LOCAL))
differ = sorted(k for k in set(LOCAL) & set(release) if LOCAL[k] != release[k])

print(json.dumps({
    "release_files": len(release),
    "local_files": len(LOCAL),
    "only_in_local": only_local,
    "only_in_release": only_release,
    "content_differs": differ,
}, ensure_ascii=False, indent=2))

print("=== release tree freshness (10 newest files) ===")
print(subprocess.run(
    ["bash", "-lc",
     "find %s -name '*.py' -printf '%%TY-%%Tm-%%TdT%%TH:%%TM %%P\\n' | sort -r | head -10" % rel],
    capture_output=True, text=True).stdout)
'''

RESTORE = r'''
import json
import subprocess

REL = "/opt/regent/current/core/src/regent/."
PKG = "/usr/local/lib/python3.12/site-packages/regent"
CONTAINERS = json.loads(r"""__CONTAINERS__""")

report = {}
for name in CONTAINERS:
    # docker cp of "dir/." merges into the target, so image files that the
    # release tree does not carry (e.g. compiled artefacts) stay untouched.
    cp = subprocess.run(["docker", "cp", REL, f"{name}:{PKG}"], capture_output=True, text=True)
    report[name] = {"cp_rc": cp.returncode, "cp_err": cp.stderr.strip()[:200]}
    if cp.returncode == 0:
        subprocess.run(["docker", "exec", name, "sh", "-c",
                        f"find {PKG} -name '__pycache__' -type d -prune -exec rm -rf {{}} +"],
                       capture_output=True, text=True)
print(json.dumps({"copied": report}, ensure_ascii=False))

for name in CONTAINERS:
    r = subprocess.run(["docker", "restart", name], capture_output=True, text=True)
    print("restart", name, r.returncode, r.stderr.strip()[:160])
'''

VERIFY = r'''
import json
import os

PROBE_MODULES = [
    "regent.application.host_guard",
    "regent.application.delivery_role_swarm",
    "regent.application.delivery_role_runtime",
    "regent.infrastructure.environment_heal_capability",
    "regent.application.hive_runtime",
    "regent.application.work_plan",
    "regent.application.diagnostic_delivery",
]
import importlib.util as u

from regent.config import get_settings, Settings

s = get_settings()
report = {
    "modules": {m: bool(u.find_spec(m)) for m in PROBE_MODULES},
    "settings_fields": {
        f: f in Settings.model_fields
        for f in ("model_thinking_mode", "model_name_2", "model_max_output_tokens")
    },
    "primary": s.model_name,
    "secondary": getattr(s, "model_name_2", os.environ.get("REGENT_MODEL_NAME_2", "")),
    "thinking_mode": getattr(s, "model_thinking_mode", "<missing>"),
}

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

    async def go():
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

    first, second = asyncio.run(go())
    report["provider_tool_path"] = {
        "model": first.model,
        "turn1_finish_reason": first.finish_reason,
        "turn1_tools": [c.name for c in first.message.tool_calls],
        "turn1_args_dict": [isinstance(c.arguments, dict) for c in first.message.tool_calls],
        "turn2_finish_reason": second.finish_reason if second else "<no tool call>",
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
    args = parser.parse_args()
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing in .env")

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
        compare = COMPARE.replace("__LOCAL_JSON__", json.dumps(local_manifest()))
        out, _ = run(ssh, "python3 - <<'PYEOF'\n" + compare + "\nPYEOF", timeout=300)
        print("=== RELEASE TREE vs LOCAL WORKING TREE ===")
        print(out)

        names, _ = run(
            ssh, "docker ps --format '{{.Names}}' | grep -E '^regent-(api|worker)' | sort"
        )
        containers = [n.strip() for n in names.splitlines() if n.strip()]
        print("targets:", containers)
        if not args.execute:
            print("dry-run: pass --execute to restore code into the containers")
            return 0

        restore = RESTORE.replace("__CONTAINERS__", json.dumps(containers))
        out, code = run(ssh, "python3 - <<'PYEOF'\n" + restore + "\nPYEOF", timeout=600)
        print("=== RESTORE ===")
        print(out)
        if code != 0:
            return code

        out, _ = run(
            ssh, "sleep 20; docker exec -i regent-worker python - <<'PYEOF'\n" + VERIFY + "\nPYEOF", timeout=300
        )
        print("=== VERIFY (regent-worker) ===")
        print(out)
        out, _ = run(
            ssh,
            "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'regent-(api|worker)'; "
            "curl -sS -m 25 http://127.0.0.1:8000/health/ready | head -c 400; echo; "
            "echo '--- worker log ---'; docker logs --tail 30 regent-worker 2>&1 | tail -30",
            timeout=180,
        )
        print(out)
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
