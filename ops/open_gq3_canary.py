"""CD-8: open GQ-3 canary (5%) on S0 after CD-6+CD-7.

Order:
1) Upsert canary keys into /opt/regent/.deploy.env (keep strategy=artifact-backed)
2) Recreate api/worker so env takes effect (worker keeps docker.sock + group-add)
3) Sync local code into containers (recreate would otherwise revive stale image code)
4) Verify settings + kill-switch policy drill (in-process; no second flip)

Does NOT set REGENT_GENERATION_STRATEGY=agentic (that would be false GQ-4).
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import time
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
_raw = dotenv_values(ROOT / ".env")
CFG = { (k.lstrip("\ufeff") if isinstance(k, str) else k): v for k, v in _raw.items() }
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
USER = CFG.get("LOGIN_USER") or "root"
PASSWORD = CFG["LOGIN_PASSWORD"]
SITE = "/usr/local/lib/python3.12/site-packages/regent"

GQ3 = {
    "REGENT_GENERATION_STRATEGY": "artifact-backed",
    "REGENT_GENERATION_STRATEGY_CANARY_GATE": "true",
    "REGENT_GENERATION_STRATEGY_CANARY_PERCENT": "20",
    "REGENT_GENERATION_STRATEGY_CANARY_VARIANT": "agentic",
    "REGENT_GENERATION_STRATEGY_KILL_SWITCH": "false",
    "REGENT_GENERATION_STRATEGY_FALLBACK": "artifact-backed",
    # CD-7.5 / GQ-3: persist egress so recreate cannot drop controlled network.
    "REGENT_DEPENDENCY_EGRESS_PROXY": "http://regent-egress:3128",
}

CD6 = {
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


def build_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        regent = ROOT / "core/src/regent"
        for path in regent.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            tar.add(path, arcname=path.relative_to(ROOT).as_posix())
        mig = ROOT / "core/migrations"
        if mig.is_dir():
            for path in mig.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    tar.add(path, arcname=path.relative_to(ROOT).as_posix())
    return buf.getvalue()


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    print(f"connected {USER}@{HOST}")

    # 1) Upsert .deploy.env
    upsert_lines = "\n".join(f"{k}={v}" for k, v in {**CD6, **GQ3}.items())
    out, code = run(
        ssh,
        f"""
set -euo pipefail
ENVF=/opt/regent/.deploy.env
touch "$ENVF"
chmod 600 "$ENVF"
python3 - <<'PY'
from pathlib import Path
path = Path("/opt/regent/.deploy.env")
text = path.read_text(encoding="utf-8") if path.exists() else ""
vals = {{}}
for line in text.splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    vals[k.strip()] = v
UPSERT = {json.dumps({**CD6, **GQ3})}
vals.update(UPSERT)
# Never silently flip default to agentic
vals["REGENT_GENERATION_STRATEGY"] = "artifact-backed"
body = "\\n".join(f"{{k}}={{v}}" for k, v in sorted(vals.items())) + "\\n"
path.write_text(body, encoding="utf-8")
print("DEPLOY_ENV_OK")
for k in sorted(UPSERT):
    print(f"{{k}}={{vals.get(k)}}")
PY
""",
    )
    print(out)
    if code != 0:
        raise SystemExit(code)

    # 2) Recreate with env + docker group
    out, code = run(
        ssh,
        r"""
set -euo pipefail
DOCKER_GID=$(getent group docker | cut -d: -f3)
echo DOCKER_GID=$DOCKER_GID
export DOCKER_GID
python3 - <<'PY'
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

def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]

def recreate(name, *, add_docker_group: bool):
    info = inspect(name)
    cfg = info["Config"]
    host = info["HostConfig"]
    image = cfg["Image"]
    env = {}
    for item in cfg.get("Env") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    env.update(file_env)
    # hard guarantees
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
    # publish ports for api if previously published
    for p in (host.get("PortBindings") or {}):
        host_ports = host["PortBindings"][p]
        if host_ports:
            hp = host_ports[0].get("HostPort")
            if hp:
                cmd += ["-p", f"{hp}:{p.split('/')[0]}"]
    if cfg.get("User"):
        cmd += ["--user", cfg["User"]]
    if cfg.get("WorkingDir"):
        cmd += ["-w", cfg["WorkingDir"]]
    cmd.append(image)
    if cfg.get("Cmd"):
        cmd += list(cfg["Cmd"])
    print("recreate", name, "group-add" if add_docker_group else "no-group")
    subprocess.check_call(cmd)

recreate("regent-api", add_docker_group=False)
recreate("regent-worker", add_docker_group=True)
print("RECREATE_OK")
PY
sleep 8
docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api|worker' || true
""",
        timeout=240,
    )
    print(out)
    if code != 0:
        raise SystemExit(code)

    # 3) Sync local code after recreate
    payload = build_tar()
    print(f"package bytes={len(payload)} sha={hashlib.sha256(payload).hexdigest()[:12]}")
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/regent-gq3-sync.tgz", "wb") as f:
        f.write(payload)
    sftp.close()
    out, code = run(
        ssh,
        r"""
set -e
rm -rf /tmp/regent-sync && mkdir -p /tmp/regent-sync
tar -xzf /tmp/regent-gq3-sync.tgz -C /tmp/regent-sync
SITE=/usr/local/lib/python3.12/site-packages/regent
SRC=/app/core/src/regent
for c in regent-api regent-worker; do
  docker cp /tmp/regent-sync/core/src/regent/. $c:$SITE/
  docker cp /tmp/regent-sync/core/src/regent/. $c:$SRC/ || true
done
docker restart regent-api regent-worker
echo SYNC_OK
""",
        timeout=300,
    )
    print(out)
    if code != 0:
        raise SystemExit(code)

    time.sleep(12)

    # 4) Verify
    out, code = run(ssh, "curl -s --max-time 8 http://127.0.0.1:8000/health/ready || true")
    print("health:", out.strip(), code)

    out, _ = run(
        ssh,
        "docker exec regent-worker printenv | grep -E "
        "'GENERATION_STRATEGY|SANDBOX_MODE|AGENT_SANDBOX|DEPENDENCY_EGRESS' | sort",
    )
    print("worker_env:\n", out.strip())

    # Prefer file-based verify (heredoc via docker exec is unreliable over SSH).
    verify_py = r"""
from urllib.parse import urlparse
from regent.config import get_settings
s = get_settings()
assert s.generation_strategy == "artifact-backed", s.generation_strategy
assert s.generation_strategy_canary_gate is True
assert int(s.generation_strategy_canary_percent) == 20
assert s.sandbox_mode == "docker"
proxy = (s.dependency_egress_proxy or "").strip()
assert proxy, "REGENT_DEPENDENCY_EGRESS_PROXY missing"
assert urlparse(proxy).scheme in {"http", "https"}, proxy
print("settings_ok", s.generation_strategy, s.generation_strategy_canary_percent, proxy)
print("GQ3_WINDOW_OPEN")
"""
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/verify_gq3_open.py", "w") as f:
        f.write(verify_py)
    sftp.close()
    out, code = run(
        ssh,
        "docker cp /tmp/verify_gq3_open.py regent-api:/tmp/verify_gq3_open.py && "
        "docker exec regent-api python /tmp/verify_gq3_open.py",
    )
    print("verify:\n", out.strip(), "exit", code)
    if code != 0 or "GQ3_WINDOW_OPEN" not in out:
        raise SystemExit("GQ-3 verify failed")

    # Hash key CD-7 files
    for rel in (
        "application/capability_acquire_service.py",
        "application/delivery_gap_recovery.py",
        "application/delivery_state.py",
        "agent/tools.py",
        "infrastructure/sandbox.py",
    ):
        local = hashlib.sha256((ROOT / "core/src/regent" / rel).read_bytes()).hexdigest()
        remote_out, _ = run(
            ssh, f"docker exec regent-api sha256sum {SITE}/{rel} | awk '{{print $1}}'"
        )
        remote = remote_out.strip()
        print(f"  {'SAME' if local == remote else 'DIFF'} {rel}")

    ssh.close()
    print("CD8_GQ3_COMPLETE")


if __name__ == "__main__":
    main()
