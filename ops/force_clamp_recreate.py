"""Force recreate api + all workers from /opt/regent/.deploy.env (canary clamp)."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = dotenv_values(ROOT / ".env")

REMOTE = r"""
set -euo pipefail
DOCKER_GID=$(getent group docker | cut -d: -f3)
export DOCKER_GID
python3 <<'PY'
import json, os, subprocess
from pathlib import Path

DOCKER_GID = os.environ["DOCKER_GID"]
envf = Path("/opt/regent/.deploy.env")
file_env = {}
for line in envf.read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    file_env[k.strip()] = v
file_env["REGENT_GENERATION_STRATEGY"] = "artifact-backed"
file_env["REGENT_GENERATION_STRATEGY_FALLBACK"] = "artifact-backed"
file_env["REGENT_GENERATION_STRATEGY_CANARY_PERCENT"] = "0"
file_env["REGENT_GENERATION_STRATEGY_CANARY_GATE"] = "false"
file_env["REGENT_GENERATION_STRATEGY_CANARY_VARIANT"] = "agentic"
file_env["REGENT_GENERATION_STRATEGY_KILL_SWITCH"] = "false"
# Qualification ladder Day0: agentic not traffic-eligible until Offline Qual.
file_env["REGENT_AGENTIC_QUALIFICATION_STATE"] = "DISABLED"
envf.write_text(
    "\n".join(f"{k}={v}" for k, v in sorted(file_env.items())) + "\n",
    encoding="utf-8",
)
for p in (Path("/opt/regent/.env"), Path("/opt/regent/.runtime.env")):
    vals = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v
    vals.update({k: file_env[k] for k in file_env if k.startswith("REGENT_GENERATION") or k.startswith("REGENT_AGENTIC")})
    p.write_text("\n".join(f"{k}={v}" for k, v in sorted(vals.items())) + "\n", encoding="utf-8")

def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]

def recreate(name, *, add_docker_group: bool):
    info = inspect(name)
    cfg, host = info["Config"], info["HostConfig"]
    env = {}
    for item in cfg.get("Env") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    env.update(file_env)
    binds = list(host.get("Binds") or [])
    if "worker" in name:
        for need in (
            "/var/run/docker.sock:/var/run/docker.sock",
            "/usr/bin/docker:/usr/bin/docker:ro",
        ):
            if not any(need.split(":")[0] in b for b in binds):
                binds.append(need)
    if name == "regent-api":
        host_console = "/opt/regent/console-dist"
        api_console = "/app/apps/regent-console/dist"
        if Path(host_console, "index.html").is_file():
            binds = [
                b for b in binds
                if not (len(b.split(":")) > 1 and b.split(":")[1].rstrip("/") == api_console.rstrip("/"))
            ]
            binds.append(f"{host_console}:{api_console}:ro")
    subprocess.check_call(["docker", "rm", "-f", name])
    cmd = ["docker", "run", "-d", "--name", name, "--network", host.get("NetworkMode") or "regent-net", "--restart", "unless-stopped"]
    for b in binds:
        cmd += ["-v", b]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    if add_docker_group:
        cmd += ["--group-add", DOCKER_GID]
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
    print("recreate", name)
    subprocess.check_call(cmd)

workers = [
    n for n in subprocess.check_output(["docker", "ps", "-a", "--format", "{{.Names}}"], text=True).splitlines()
    if n.startswith("regent-worker")
]
recreate("regent-api", add_docker_group=False)
for w in sorted(workers):
    recreate(w, add_docker_group=True)
print("RECREATE_OK", workers)
PY
sleep 12
echo '--- settings ---'
docker exec regent-api python -c 'from regent.config import get_settings; s=get_settings(); print(s.generation_strategy, s.generation_strategy_canary_percent, s.generation_strategy_canary_gate, getattr(s,"agentic_qualification_state","?"))'
docker exec regent-api printenv | grep -E 'GENERATION_STRATEGY_CANARY|AGENTIC_QUAL' | sort
curl -s --max-time 8 http://127.0.0.1:8000/health/ready; echo
"""


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username="root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    _, o, e = ssh.exec_command(REMOTE, timeout=360)
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
