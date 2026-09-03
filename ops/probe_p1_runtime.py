"""Verify P1 runtime settings on S0."""

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

CMD = r"""
echo '=== health ==='
curl -sS --max-time 8 http://127.0.0.1:8000/v1/health; echo
echo '=== settings ==='
docker exec -i regent-api python - <<'PY'
from regent.config import get_settings
from regent.application.delivery_success_policy import effective_max_concurrent_generating
s = get_settings()
print({
  'model_name': s.model_name,
  'model_base_url': s.model_base_url,
  'model_timeout_seconds': s.model_timeout_seconds,
  'max_concurrent_generating': s.max_concurrent_generating,
  'worker_replicas': s.worker_replicas,
  'worker_dispatch_concurrency': s.worker_dispatch_concurrency,
  'effective_cap': effective_max_concurrent_generating(s),
})
PY
echo '=== console asset ==='
curl -s --max-time 5 http://127.0.0.1:8000/console/ | head -c 300; echo
docker exec regent-api ls /app/apps/regent-console/dist/assets | head -5
"""


def main() -> int:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    _, o, e = ssh.exec_command(CMD, timeout=60)
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
