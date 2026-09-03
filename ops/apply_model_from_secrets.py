"""Apply /opt/regent/.secrets.env model settings into live runtime and recreate containers.

Why DeepSeek is still used:
  - GLM was written to /.secrets.env
  - Running api/worker env comes from baked Config.Env + /.env + /.runtime.env
    which still point at api.deepseek.com / deepseek-v4-pro
  - compose env_file is .env; secrets.env is NOT auto-loaded
  - docker restart does not reload env; recreate is required

Usage:
  python ops/apply_model_from_secrets.py           # dry-run
  python ops/apply_model_from_secrets.py --execute
"""

from __future__ import annotations

import argparse
import sys
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

REMOTE = r'''
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

EXECUTE = __EXECUTE__
MODEL_KEYS = (
    "REGENT_MODEL_PROVIDER",
    "REGENT_MODEL_BASE_URL",
    "REGENT_MODEL_NAME",
    "REGENT_MODEL_NAME_2",
    "REGENT_MODEL_NAME_3",
    "REGENT_MODEL_API_KEY",
    "REGENT_MODEL_TIMEOUT_SECONDS",
)

def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        # allow leading space
        if raw.startswith("REGENT_") or raw.split("=", 1)[0].strip().startswith("REGENT_"):
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out

def write_env(path: Path, vals: dict[str, str]) -> None:
    # Preserve order: existing keys updated in place; new keys appended.
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in vals:
                out.append(f"{k}={vals[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in vals.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

secrets = load_env(Path("/opt/regent/.secrets.env"))
model = {k: secrets[k] for k in MODEL_KEYS if k in secrets and secrets[k].strip()}
if "REGENT_MODEL_BASE_URL" not in model or "REGENT_MODEL_NAME" not in model or "REGENT_MODEL_API_KEY" not in model:
    raise SystemExit(f"secrets.env missing required model fields; have={sorted(model)}")

report = {
    "secrets_model_name": model.get("REGENT_MODEL_NAME"),
    "secrets_model_name_2": model.get("REGENT_MODEL_NAME_2"),
    "secrets_model_name_3": model.get("REGENT_MODEL_NAME_3"),
    "secrets_base_url": model.get("REGENT_MODEL_BASE_URL"),
    "secrets_provider": model.get("REGENT_MODEL_PROVIDER"),
    "key_len": len(model.get("REGENT_MODEL_API_KEY") or ""),
}
print(json.dumps({"plan": report}, ensure_ascii=False))

if not EXECUTE:
    # show current live
    live = subprocess.check_output(
        ["docker", "exec", "regent-worker", "printenv", "REGENT_MODEL_NAME"],
        text=True,
    ).strip()
    base = subprocess.check_output(
        ["docker", "exec", "regent-worker", "printenv", "REGENT_MODEL_BASE_URL"],
        text=True,
    ).strip()
    print(json.dumps({"dry_run": True, "live_model_name": live, "live_base_url": base}, ensure_ascii=False))
    raise SystemExit(0)

# 1) Persist into files containers actually use
for p in (Path("/opt/regent/.env"), Path("/opt/regent/.runtime.env")):
    write_env(p, model)
    print("updated", p)

# Also put into deploy.env so scale_workers recreate keeps them
write_env(Path("/opt/regent/.deploy.env"), {
    k: v for k, v in model.items() if k != "REGENT_MODEL_API_KEY"
})
# API key belongs in secrets/runtime/.env — still put in deploy so recreate has it
# (deploy.env is root-only on this host)
write_env(Path("/opt/regent/.deploy.env"), model)

# 2) Recreate api + all workers with secrets overlay last
DOCKER_GID = "0"
try:
    import grp
    DOCKER_GID = str(grp.getgrnam("docker").gr_gid)
except Exception:
    st = os.stat("/var/run/docker.sock")
    DOCKER_GID = str(st.st_gid)

def inspect(name: str) -> dict:
    return json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]

def list_workers() -> list[str]:
    names = subprocess.check_output(["docker", "ps", "-a", "--format", "{{.Names}}"], text=True).splitlines()
    return sorted([n for n in names if n == "regent-worker" or n.startswith("regent-worker-")])

def recreate(name: str, *, add_docker_group: bool) -> None:
    info = inspect(name)
    cfg = info["Config"]
    host = info["HostConfig"]
    env: dict[str, str] = {}
    for item in cfg.get("Env") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    # overlays: runtime then deploy then secrets (secrets wins)
    for path in (
        Path("/opt/regent/.runtime.env"),
        Path("/opt/regent/.deploy.env"),
        Path("/opt/regent/.secrets.env"),
        Path("/opt/regent/.env"),
    ):
        env.update(load_env(path))
    # force model from secrets
    env.update(model)

    net = host.get("NetworkMode") or "regent-net"
    binds = list(host.get("Binds") or [])
    if "worker" in name:
        for need in (
            "/var/run/docker.sock:/var/run/docker.sock",
            "/usr/bin/docker:/usr/bin/docker:ro",
        ):
            if not any(need.split(":")[0] in b for b in binds):
                binds.append(need)
    # Durable console: host tree survives recreate (avoid Jul-27 image UI rollback).
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
            print("console_bind", binds[-1])
        else:
            print("WARN: missing /opt/regent/console-dist; api will use image console")

    subprocess.check_call(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = ["docker", "run", "-d", "--name", name, "--network", net, "--restart", "unless-stopped"]
    if add_docker_group:
        cmd += ["--group-add", DOCKER_GID]
    for b in binds:
        cmd += ["-v", b]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    for p, hosts in (host.get("PortBindings") or {}).items():
        if hosts and hosts[0].get("HostPort"):
            cmd += ["-p", f"{hosts[0]['HostPort']}:{p.split('/')[0]}"]
    if cfg.get("User"):
        cmd += ["--user", cfg["User"]]
    if cfg.get("WorkingDir"):
        cmd += ["-w", cfg["WorkingDir"]]
    cmd.append(cfg["Image"])
    if cfg.get("Cmd"):
        cmd += list(cfg["Cmd"])
    print("CREATE", name)
    subprocess.check_call(cmd)

recreate("regent-api", add_docker_group=False)
for w in list_workers() or ["regent-worker"]:
    recreate(w, add_docker_group=True)

print(json.dumps({"executed": True, "applied": report}, ensure_ascii=False))
'''


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[str, int]:
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    return out, o.channel.recv_exit_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")

    remote = REMOTE.replace("__EXECUTE__", "True" if args.execute else "False")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=CFG.get("LOGIN_USER") or "root", password=PASSWORD, timeout=30)

    out, code = run(ssh, "python3 - <<'PY'\n" + remote + "\nPY", timeout=300)
    print(out)
    if code != 0:
        ssh.close()
        return code

    if args.execute:
        import time

        time.sleep(10)
        probe = r'''
import httpx
from regent.config import get_settings
s = get_settings()
key = s.model_api_key.get_secret_value() if s.model_api_key else ""
base = (s.model_base_url or "").rstrip("/")
model = s.model_name or ""
print("live_base_url", base)
print("live_model_name", model)
print("key_len", len(key))
url = base + "/chat/completions"
# some gateways want /v1 prefix already in base
r = httpx.post(
    url,
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8},
    timeout=45.0,
)
print("probe_status", r.status_code)
print("probe_body", r.text[:240])
'''
        out, _ = run(
            ssh,
            "docker exec -i regent-worker python - <<'PY'\n" + probe + "\nPY",
            timeout=90,
        )
        print("=== LIVE PROBE ===")
        print(out)
        out, _ = run(
            ssh,
            "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'regent-(api|worker)'",
        )
        print(out)

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
