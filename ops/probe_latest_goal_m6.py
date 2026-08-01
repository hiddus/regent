"""Inspect the newest Goal against M6 canary-watch requirements."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
REMOTE_SRC = ROOT / "ops" / "_remote_probe_latest_goal_m6.py"
OPENED = "2026-08-01T14:38:33+00:00"


def main() -> None:
    remote = REMOTE_SRC.read_text(encoding="utf-8").replace("__OPENED__", OPENED)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/probe_latest_goal_m6.py", "w") as f:
        f.write(remote)
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/probe_latest_goal_m6.py regent-api:/tmp/probe_latest_goal_m6.py && "
        "docker exec -w /app regent-api python /tmp/probe_latest_goal_m6.py",
        timeout=120,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    ssh.close()


if __name__ == "__main__":
    main()
