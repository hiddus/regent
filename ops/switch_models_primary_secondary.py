"""Switch production primary + secondary models.

Key / base URL unchanged. Updates env files and recreates api + workers.

Usage:
  python ops/switch_models_primary_secondary.py            # dry-run
  python ops/switch_models_primary_secondary.py --execute
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

PRIMARY = "deepseek-v4.1-flash-tx-260911"
SECONDARY = "glm-5.3-tx-20260821"

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
        if k in {"REGENT_MODEL_NAME", "REGENT_MODEL_NAME_2", "REGENT_MODEL_BASE_URL"}
    }
    for p in ENV_FILES
}
print(json.dumps({"before_files": before}, ensure_ascii=False))


def live_env(container: str) -> dict[str, str]:
    keys = ("REGENT_MODEL_BASE_URL", "REGENT_MODEL_NAME", "REGENT_MODEL_NAME_2")
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
    workers = sorted(
        n for n in names if n == "regent-worker" or n.startswith("regent-worker-")
    )
    return api + workers


targets = container_names()
print(
    json.dumps(
        {"before_live": live_env("regent-worker"), "targets": targets},
        ensure_ascii=False,
    )
)

if not EXECUTE:
    print(json.dumps({"dry_run": True, "would_set": SET_VALUES}, ensure_ascii=False))
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

    binds = list(host.get("Binds") or [])
    if "worker" in name:
        for need in (
            "/var/run/docker.sock:/var/run/docker.sock",
            "/usr/bin/docker:/usr/bin/docker:ro",
        ):
            if not any(need.split(":")[0] in b for b in binds):
                binds.append(need)
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

print(
    json.dumps(
        {"recreated": targets, "after_live": live_env("regent-worker")},
        ensure_ascii=False,
    )
)
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    remote = (
        REMOTE.replace("__EXECUTE__", "True" if args.execute else "False")
        .replace("__PRIMARY__", json.dumps(PRIMARY))
        .replace("__SECONDARY__", json.dumps(SECONDARY))
    )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    try:
        sftp = client.open_sftp()
        with sftp.file("/tmp/_switch_models.py", "w") as fh:
            fh.write(remote)
        sftp.close()
        print(
            f"[{'EXECUTE' if args.execute else 'DRY-RUN'}] "
            f"primary -> {PRIMARY}; secondary -> {SECONDARY}"
        )
        stdin, stdout, stderr = client.exec_command(
            "python3 /tmp/_switch_models.py", timeout=600
        )
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        print(out)
        if err.strip():
            print("STDERR:", err[:2000])
        if code != 0:
            return code
        if args.execute:
            time.sleep(8)
            stdin, stdout, stderr = client.exec_command(
                "echo api:; docker exec regent-api printenv REGENT_MODEL_NAME REGENT_MODEL_NAME_2 REGENT_MODEL_BASE_URL; "
                "echo worker:; docker exec regent-worker printenv REGENT_MODEL_NAME REGENT_MODEL_NAME_2; "
                "echo w2:; docker exec regent-worker-2 printenv REGENT_MODEL_NAME REGENT_MODEL_NAME_2; "
                "echo w3:; docker exec regent-worker-3 printenv REGENT_MODEL_NAME REGENT_MODEL_NAME_2; "
                "curl -sf http://localhost:8000/health || curl -sf http://localhost:8000/v1/health || true",
                timeout=60,
            )
            print("VERIFY:")
            print(stdout.read().decode("utf-8", errors="replace"))
            print(stderr.read().decode("utf-8", errors="replace")[:500])
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
