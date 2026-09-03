"""Restart api after env knobs; verify worker/api settings."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}

REMOTE = r'''
set -e
docker restart regent-api
sleep 8
curl -sS --max-time 10 http://127.0.0.1:8000/v1/health; echo
echo '=== api settings ==='
docker exec -i regent-api python - <<'PY'
from regent.config import get_settings
from regent.application.delivery_success_policy import effective_max_concurrent_generating
s = get_settings()
print({
  "model_timeout_seconds": s.model_timeout_seconds,
  "max_concurrent_generating": s.max_concurrent_generating,
  "effective_cap": effective_max_concurrent_generating(s),
  "model_name": s.model_name,
})
PY
echo '=== worker env ==='
docker exec regent-worker printenv | grep -E 'MODEL_TIMEOUT|MAX_CONCURRENT|MODEL_NAME|MODEL_BASE' || true
'''


def main() -> int:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    _, o, e = ssh.exec_command(REMOTE, timeout=120)
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
