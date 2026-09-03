"""Fetch GenerationRunRequested failure for the new goal."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
REMOTE_SRC = ROOT / "ops" / "_remote_probe_gen_fail.py"


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/_gen_fail.py", "w") as f:
        f.write(REMOTE_SRC.read_text(encoding="utf-8"))
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/_gen_fail.py regent-api:/tmp/_gen_fail.py && "
        "docker exec -w /app -e PYTHONIOENCODING=utf-8 regent-api "
        "python /tmp/_gen_fail.py",
        timeout=60,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    ssh.close()


if __name__ == "__main__":
    main()
