"""Upload and run _remote_requeue_encyclopedia.py on S0."""

from __future__ import annotations

import sys
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
REMOTE_SRC = ROOT / "ops" / "_remote_requeue_encyclopedia.py"


def main() -> int:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    sftp.put(str(REMOTE_SRC), "/tmp/_remote_requeue_encyclopedia.py")
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/_remote_requeue_encyclopedia.py "
        "regent-api:/tmp/_remote_requeue_encyclopedia.py "
        "&& docker exec -w /tmp regent-api python _remote_requeue_encyclopedia.py",
        timeout=60,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
