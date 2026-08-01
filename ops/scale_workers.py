"""Scale S0 regent-worker fleet for parallel Outbox consumption.

Clones the primary ``regent-worker`` container (image, binds, env, network,
docker.sock) into ``regent-worker-2`` … ``regent-worker-N``.

Usage:
  python ops/scale_workers.py              # dry-run (show plan)
  python ops/scale_workers.py --replicas 3 --execute
  python ops/scale_workers.py --replicas 1 --execute   # tear down extras

Also writes REGENT_MAX_CONCURRENT_GENERATING / DISPATCH_CONCURRENCY into
/opt/regent/.deploy.env so recreate scripts keep parallelism settings.
"""

from __future__ import annotations

import argparse
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
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

REMOTE_PY = r'''
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPLICAS = __REPLICAS__
EXECUTE = __EXECUTE__
DISPATCH_CONCURRENCY = __DISPATCH_CONCURRENCY__
MAX_GENERATING = __MAX_GENERATING__

PRIMARY = "regent-worker"


def sh(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def ensure_kv(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def list_workers() -> list[str]:
    names = sh(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    workers = [n for n in names if n == PRIMARY or n.startswith(PRIMARY + "-")]
    def sort_key(n: str) -> tuple[int, str]:
        if n == PRIMARY:
            return (0, n)
        suffix = n[len(PRIMARY) + 1 :]
        return (1, suffix.zfill(4) if suffix.isdigit() else suffix)
    return sorted(workers, key=sort_key)


def desired_names(n: int) -> list[str]:
    if n <= 0:
        raise SystemExit("replicas must be >= 1")
    names = [PRIMARY]
    for i in range(2, n + 1):
        names.append(f"{PRIMARY}-{i}")
    return names


def inspect(name: str) -> dict:
    return json.loads(sh(["docker", "inspect", name]))[0]


def docker_gid() -> str:
    try:
        st = os.stat("/var/run/docker.sock")
        return str(st.st_gid)
    except OSError:
        return "0"


def clone_from_primary(name: str, primary: dict) -> None:
    cfg = primary["Config"]
    host = primary["HostConfig"]
    image = cfg["Image"]
    env: dict[str, str] = {}
    for item in cfg.get("Env") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    # Overlay deploy.env
    envf = Path("/opt/regent/.deploy.env")
    if envf.is_file():
        for line in envf.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v
    env["REGENT_WORKER_DISPATCH_CONCURRENCY"] = str(DISPATCH_CONCURRENCY)
    env["REGENT_MAX_CONCURRENT_GENERATING"] = str(MAX_GENERATING)
    env["REGENT_WORKER_REPLICAS"] = str(REPLICAS)
    env["REGENT_WORKER_REPLICA_NAME"] = name

    net = host.get("NetworkMode") or "regent-net"
    binds = list(host.get("Binds") or [])
    for need in (
        "/var/run/docker.sock:/var/run/docker.sock",
        "/usr/bin/docker:/usr/bin/docker:ro",
    ):
        if not any(need.split(":")[0] in b for b in binds):
            binds.append(need)

    subprocess.check_call(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--network", net,
        "--restart", "unless-stopped",
        "--group-add", docker_gid(),
    ]
    for b in binds:
        cmd += ["-v", b]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    if cfg.get("User"):
        cmd += ["--user", cfg["User"]]
    if cfg.get("WorkingDir"):
        cmd += ["-w", cfg["WorkingDir"]]
    cmd.append(image)
    if cfg.get("Cmd"):
        cmd += list(cfg["Cmd"])
    print("CREATE", name)
    subprocess.check_call(cmd)


def patch_primary_env(primary_name: str) -> None:
    """Recreate primary with updated concurrency env (same image/binds)."""
    info = inspect(primary_name)
    clone_from_primary(primary_name, info)


def main() -> None:
    current = list_workers()
    want = desired_names(REPLICAS)
    print("current=", current)
    print("desired=", want)
    ensure_kv(Path("/opt/regent/.deploy.env"), "REGENT_WORKER_DISPATCH_CONCURRENCY", str(DISPATCH_CONCURRENCY))
    ensure_kv(Path("/opt/regent/.deploy.env"), "REGENT_MAX_CONCURRENT_GENERATING", str(MAX_GENERATING))
    ensure_kv(Path("/opt/regent/.deploy.env"), "REGENT_WORKER_REPLICAS", str(REPLICAS))
    if not EXECUTE:
        print("DRY_RUN — pass --execute to apply")
        return
    if PRIMARY not in current and PRIMARY not in want:
        raise SystemExit("primary regent-worker missing")
    if PRIMARY not in current:
        raise SystemExit("cannot scale: primary regent-worker does not exist")

    primary = inspect(PRIMARY)
    # Refresh primary env for concurrency knobs
    patch_primary_env(PRIMARY)
    primary = inspect(PRIMARY)

    for name in want:
        if name == PRIMARY:
            continue
        clone_from_primary(name, primary)

    for name in current:
        if name not in want:
            print("REMOVE", name)
            subprocess.check_call(["docker", "rm", "-f", name])

    print("WORKERS:")
    print(sh(["docker", "ps", "--format", "{{.Names}} {{.Status}}", "--filter", "name=regent-worker"]))
    print("SCALE_OK", REPLICAS)


if __name__ == "__main__":
    main()
'''


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 180) -> tuple[str, int]:
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    return out, o.channel.recv_exit_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replicas", type=int, default=3, help="Total worker containers (default 3)")
    parser.add_argument("--dispatch-concurrency", type=int, default=2)
    parser.add_argument(
        "--max-generating",
        type=int,
        default=0,
        help="Max concurrent GENERATING runs (0=auto: replicas×dispatch×2)",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")

    remote = (
        REMOTE_PY.replace("__REPLICAS__", str(args.replicas))
        .replace("__EXECUTE__", "True" if args.execute else "False")
        .replace("__DISPATCH_CONCURRENCY__", str(args.dispatch_concurrency))
        .replace("__MAX_GENERATING__", str(args.max_generating))
    )

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    try:
        sftp = ssh.open_sftp()
        with sftp.file("/tmp/scale_workers.py", "w") as f:
            f.write(remote)
        sftp.close()
        out, code = run(ssh, "python3 /tmp/scale_workers.py", timeout=300)
        print(out)
        return code
    finally:
        ssh.close()


if __name__ == "__main__":
    sys.exit(main())
