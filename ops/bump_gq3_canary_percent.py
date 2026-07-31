"""Bump GQ-3 canary percent on S0 (keeps default artifact-backed).

After recreate: always redeploy console so /console/ does not fall back to image UI.
Does NOT flip REGENT_GENERATION_STRATEGY=agentic.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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
PASSWORD = CFG["LOGIN_PASSWORD"]

BASE_ENV = {
    "REGENT_GENERATION_STRATEGY": "artifact-backed",
    "REGENT_GENERATION_STRATEGY_CANARY_GATE": "true",
    "REGENT_GENERATION_STRATEGY_CANARY_VARIANT": "agentic",
    "REGENT_GENERATION_STRATEGY_KILL_SWITCH": "false",
    "REGENT_GENERATION_STRATEGY_FALLBACK": "artifact-backed",
    "REGENT_DEPENDENCY_EGRESS_PROXY": "http://regent-egress:3128",
    "REGENT_SANDBOX_MODE": "docker",
    "REGENT_AGENT_SANDBOX_IMAGE": "regent-agent-exec-v1:1",
    "REGENT_HOST_PATH_MAP": "/opt/regent=/opt/regent",
    "REGENT_WORKSPACE_ROOT": "/opt/regent/workspaces",
    "REGENT_BUILD_ROOT": "/opt/regent/builds",
    "REGENT_ARTIFACT_ROOT": "/opt/regent/artifacts",
}


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[str, int]:
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    return out, o.channel.recv_exit_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--percent", type=int, default=20, choices=range(1, 101), metavar="N")
    parser.add_argument("--skip-console", action="store_true")
    args = parser.parse_args()
    percent = int(args.percent)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30)

    upsert = {**BASE_ENV, "REGENT_GENERATION_STRATEGY_CANARY_PERCENT": str(percent)}
    out, code = run(
        ssh,
        f"""
set -euo pipefail
python3 - <<'PY'
from pathlib import Path
import json
path = Path("/opt/regent/.deploy.env")
vals = {{}}
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v
vals.update(json.loads({json.dumps(json.dumps(upsert))}))
vals["REGENT_GENERATION_STRATEGY"] = "artifact-backed"
path.write_text("\\n".join(f"{{k}}={{v}}" for k, v in sorted(vals.items())) + "\\n", encoding="utf-8")
print("PERCENT", vals.get("REGENT_GENERATION_STRATEGY_CANARY_PERCENT"))
print("EGRESS", vals.get("REGENT_DEPENDENCY_EGRESS_PROXY"))
PY
""",
    )
    print(out)
    if code != 0:
        raise SystemExit(code)

    out, code = run(
        ssh,
        r"""
set -euo pipefail
DOCKER_GID=$(getent group docker | cut -d: -f3)
export DOCKER_GID
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
DOCKER_GID = os.environ["DOCKER_GID"]
file_env = {}
for line in Path("/opt/regent/.deploy.env").read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    file_env[k.strip()] = v

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
    env["REGENT_GENERATION_STRATEGY"] = "artifact-backed"
    net = host.get("NetworkMode") or "regent-net"
    binds = list(host.get("Binds") or [])
    if name == "regent-worker":
        for need in (
            "/var/run/docker.sock:/var/run/docker.sock",
            "/usr/bin/docker:/usr/bin/docker:ro",
        ):
            if not any(need.split(":")[0] in b for b in binds):
                binds.append(need)
    subprocess.check_call(["docker", "rm", "-f", name])
    cmd = ["docker", "run", "-d", "--name", name, "--network", net, "--restart", "unless-stopped"]
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

recreate("regent-api", add_docker_group=False)
recreate("regent-worker", add_docker_group=True)
print("RECREATE_OK")
PY
sleep 8
""",
        timeout=240,
    )
    print(out)
    if code != 0:
        raise SystemExit(code)

    verify = r"""
import os
pct = os.environ.get("REGENT_GENERATION_STRATEGY_CANARY_PERCENT", "")
gate = os.environ.get("REGENT_GENERATION_STRATEGY_CANARY_GATE", "")
strat = os.environ.get("REGENT_GENERATION_STRATEGY", "")
proxy = os.environ.get("REGENT_DEPENDENCY_EGRESS_PROXY", "")
assert strat == "artifact-backed", strat
assert gate.lower() in {"1", "true", "yes"}, gate
assert pct == "__PCT__", pct
assert proxy.startswith("http"), proxy
print("VERIFY_OK", pct, proxy)
""".replace("__PCT__", str(percent))
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/verify_pct.py", "wb") as f:
        f.write(verify.encode())
    sftp.close()
    out, code = run(
        ssh,
        "docker cp /tmp/verify_pct.py regent-api:/tmp/verify_pct.py && "
        "docker exec regent-api python /tmp/verify_pct.py",
    )
    print(out)
    if code != 0 or "VERIFY_OK" not in out:
        ssh.close()
        raise SystemExit("percent verify failed")

    # Recreate revived image code; sync local Python so report/helpers stay current.
    print("syncing python package…")
    sync = subprocess.run(
        [sys.executable, str(ROOT / "ops" / "sync_local_to_server.py")], cwd=ROOT
    )
    if sync.returncode != 0:
        print("WARN: sync_local_to_server failed; continuing to console redeploy")

    ssh.close()

    if not args.skip_console:
        print("redeploying console…")
        r = subprocess.run([sys.executable, str(ROOT / "ops" / "deploy_console.py")], cwd=ROOT)
        if r.returncode != 0:
            raise SystemExit("deploy_console failed — /console/ may be stale")
    print(f"CANARY_PERCENT_SET={percent}")


if __name__ == "__main__":
    main()
