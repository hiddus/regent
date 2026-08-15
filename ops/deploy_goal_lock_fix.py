"""Deploy only the reviewed goal-readiness fix to the production API."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = dotenv_values(ROOT / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]
FILES = (
    "core/src/regent/application/goal_readiness.py",
    "core/src/regent/application/app_project_service.py",
    "core/src/regent/application/app_guidance_service.py",
)


def main() -> None:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for relative in FILES:
            archive.add(ROOT / relative, arcname=relative)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    with ssh.open_sftp().file("/tmp/regent-goal-lock-fix.tgz", "wb") as remote:
        remote.write(payload.getvalue())
    command = r"""
set -e
rm -rf /tmp/regent-goal-lock-fix
mkdir -p /tmp/regent-goal-lock-fix
tar -xzf /tmp/regent-goal-lock-fix.tgz -C /tmp/regent-goal-lock-fix
REL=$(readlink -f /opt/regent/current)
for name in goal_readiness.py app_project_service.py app_guidance_service.py; do
  src=/tmp/regent-goal-lock-fix/core/src/regent/application/$name
  docker cp "$src" regent-api:/usr/local/lib/python3.12/site-packages/regent/application/$name
  docker cp "$src" regent-api:/app/core/src/regent/application/$name
  cp "$src" "$REL/core/src/regent/application/$name"
  cp "$src" "/opt/regent/core/src/regent/application/$name"
done
docker restart regent-api
for i in $(seq 1 15); do
  code=$(curl -sS -o /tmp/regent-goal-lock-health.json -w '%{http_code}' http://127.0.0.1:8000/health/ready || true)
  if [ "$code" = 200 ]; then cat /tmp/regent-goal-lock-health.json; exit 0; fi
  sleep 2
done
exit 1
"""
    _, stdout, stderr = ssh.exec_command(command, timeout=90)
    output = (stdout.read() + stderr.read()).decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    ssh.close()
    print(output)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
