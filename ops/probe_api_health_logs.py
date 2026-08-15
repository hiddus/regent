"""Read-only API container status, health, and recent startup logs."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    command = (
        "docker inspect -f '{{.State.Status}} {{.State.Restarting}} {{.State.ExitCode}}' regent-api; "
        "curl -sS -m 5 -w '\nHTTP=%{http_code}\n' http://127.0.0.1:8000/health/ready || true; "
        "docker logs --tail 100 regent-api 2>&1"
    )
    _, stdout, stderr = ssh.exec_command(command, timeout=60)
    print((stdout.read() + stderr.read()).decode("utf-8", "replace"))
    ssh.close()


if __name__ == "__main__":
    main()
