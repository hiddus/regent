"""Set REGENT_GENERATION_STRATEGY on S0 (artifact-backed | agentic).

Always preserves egress + canary gate knobs from .deploy.env merge.
Always redeploys console after recreate (unless --skip-console).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[str, int]:
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    return (o.read() + e.read()).decode("utf-8", "replace"), o.channel.recv_exit_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=("artifact-backed", "agentic"), required=True)
    parser.add_argument("--skip-console", action="store_true")
    args = parser.parse_args()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    patch = {
        "REGENT_GENERATION_STRATEGY": args.strategy,
        "REGENT_GENERATION_STRATEGY_FALLBACK": "artifact-backed",
        "REGENT_DEPENDENCY_EGRESS_PROXY": "http://regent-egress:3128",
    }
    out, code = run(
        ssh,
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "import json\n"
        f"patch = json.loads({json.dumps(json.dumps(patch))})\n"
        "path = Path('/opt/regent/.deploy.env')\n"
        "vals = {}\n"
        "if path.exists():\n"
        "  for line in path.read_text(encoding='utf-8').splitlines():\n"
        "    if not line.strip() or line.strip().startswith('#') or '=' not in line: continue\n"
        "    k,v = line.split('=',1); vals[k.strip()]=v\n"
        "vals.update(patch)\n"
        "path.write_text('\\n'.join(f'{k}={v}' for k,v in sorted(vals.items()))+'\\n', encoding='utf-8')\n"
        "print('STRATEGY', vals['REGENT_GENERATION_STRATEGY'])\n"
        "print('EGRESS', vals.get('REGENT_DEPENDENCY_EGRESS_PROXY'))\n"
        "PY",
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
    binds = list(host.get("Binds") or [])
    if name == "regent-worker":
        for need in (
            "/var/run/docker.sock:/var/run/docker.sock",
            "/usr/bin/docker:/usr/bin/docker:ro",
        ):
            if not any(need.split(":")[0] in b for b in binds):
                binds.append(need)
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

recreate("regent-api", add_docker_group=False)
recreate("regent-worker", add_docker_group=True)
print("RECREATE_OK")
PY
sleep 8
docker exec regent-api printenv REGENT_GENERATION_STRATEGY
docker exec regent-api printenv REGENT_DEPENDENCY_EGRESS_PROXY
""",
        timeout=240,
    )
    print(out)
    ssh.close()
    if code != 0:
        raise SystemExit(code)
    if args.strategy not in out:
        raise SystemExit("strategy verify failed")

    if not args.skip_console:
        r = subprocess.run([sys.executable, str(ROOT / "ops" / "deploy_console.py")], cwd=ROOT)
        if r.returncode != 0:
            raise SystemExit("deploy_console failed")
    print(f"STRATEGY_SET={args.strategy}")


if __name__ == "__main__":
    main()
