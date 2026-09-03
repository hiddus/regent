"""Recreate api + workers so live REGENT_HOST_PATH_MAP matches /opt/regent/.env.

Symlink /var/lib/regent -> /opt/regent already unblocks sandbox binds; this recreate
makes the in-container Settings match docs/deployment.md without relying on the symlink.

Usage:
  python ops/recreate_with_host_path_map_2026_08_11.py [--dry-run]
"""

from __future__ import annotations

import argparse
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
WANT = "/var/lib/regent=/opt/regent;/opt/regent=/opt/regent"

REMOTE = r'''
import json, subprocess
from pathlib import Path

WANT = __WANT__
NAMES = ["regent-api", "regent-worker", "regent-worker-2", "regent-worker-3"]

def inspect(name):
    raw = subprocess.check_output(["docker", "inspect", name], text=True)
    return json.loads(raw)[0]

def load_env_files():
    out = {}
    for p in (Path("/opt/regent/.env"), Path("/opt/regent/.runtime.env"), Path("/opt/regent/.deploy.env"), Path("/opt/regent/.secrets.env")):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out

file_env = load_env_files()
file_env["REGENT_HOST_PATH_MAP"] = WANT
# keep .env on disk aligned
env_path = Path("/opt/regent/.env")
text = env_path.read_text(encoding="utf-8")
import re
if re.search(r"^REGENT_HOST_PATH_MAP=.*$", text, flags=re.M):
    text = re.sub(r"^REGENT_HOST_PATH_MAP=.*$", f"REGENT_HOST_PATH_MAP={WANT}", text, flags=re.M)
else:
    text += f"\nREGENT_HOST_PATH_MAP={WANT}\n"
env_path.write_text(text, encoding="utf-8")

def recreate(name: str) -> None:
    info = inspect(name)
    cfg = info["Config"]
    host = info["HostConfig"]
    env = {}
    for item in cfg.get("Env") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    env.update(file_env)
    env["REGENT_HOST_PATH_MAP"] = WANT
    binds = list(host.get("Binds") or [])
    if "worker" in name:
        for need in ("/var/run/docker.sock:/var/run/docker.sock", "/usr/bin/docker:/usr/bin/docker:ro"):
            if not any(need.split(":")[0] in b for b in binds):
                binds.append(need)
    subprocess.check_call(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL)
    cmd = ["docker", "run", "-d", "--name", name, "--network", host.get("NetworkMode") or "regent-net", "--restart", "unless-stopped"]
    # docker.sock group
    for g in host.get("GroupAdd") or []:
        cmd += ["--group-add", str(g)]
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
    subprocess.check_call(cmd)

if __DRY__:
    print(json.dumps({"dry_run": True, "want": WANT, "file_map": file_env.get("REGENT_HOST_PATH_MAP")}, ensure_ascii=False))
else:
    for n in NAMES:
        recreate(n)
    report = {}
    for n in NAMES:
        raw = subprocess.check_output(
            ["docker", "exec", n, "sh", "-lc", "printf %s \"$REGENT_HOST_PATH_MAP\""],
            text=True,
        ).strip()
        report[n] = raw
    print(json.dumps({"recreated": NAMES, "live_maps": report}, ensure_ascii=False))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")
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
        script = REMOTE.replace("__WANT__", json_dumps(WANT)).replace(
            "__DRY__", "True" if args.dry_run else "False"
        )
        # write to remote tempfile then run to avoid quoting hell
        sftp = ssh.open_sftp()
        with sftp.file("/tmp/recreate_host_path_map.py", "w") as f:
            f.write(script)
        sftp.close()
        _, out, err = ssh.exec_command("python3 /tmp/recreate_host_path_map.py", timeout=300)
        print(out.read().decode("utf-8", "replace"))
        e = err.read().decode("utf-8", "replace").strip()
        if e:
            print("STDERR", e[:1500])
        # health
        _, o2, _ = ssh.exec_command(
            "sleep 3; curl -fsS http://127.0.0.1:8000/v1/health | head -c 400; echo; "
            "docker exec -i regent-worker sh -lc 'docker run --rm --entrypoint sh --network none "
            "--mount type=bind,src=/var/lib/regent/workspaces/projects/c3af7c4e-74f7-46b8-bf1d-fd2977940b09/agent,dst=/workspace "
            "-w /workspace regent-agent-exec-v1:1 -lc \"echo POST_RECREATE_SANDBOX_OK\"' 2>&1 | tail -5",
            timeout=180,
        )
        print(o2.read().decode("utf-8", "replace")[:2000])
    finally:
        ssh.close()
    return 0


def json_dumps(s: str) -> str:
    import json

    return json.dumps(s)


if __name__ == "__main__":
    raise SystemExit(main())
