"""Apply P1 runtime knobs on S0 and reclaim expired DISPATCHING GenRun events."""

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

REMOTE = r'''
set -e
ensure_kv() {
  local f="$1" k="$2" v="$3"
  touch "$f"
  if grep -q "^${k}=" "$f" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$f"
  else
    echo "${k}=${v}" >> "$f"
  fi
}
for ENVF in /opt/regent/.deploy.env /opt/regent/.runtime.env /opt/regent/.env; do
  ensure_kv "$ENVF" REGENT_MODEL_TIMEOUT_SECONDS 300
  ensure_kv "$ENVF" REGENT_MAX_CONCURRENT_GENERATING 8
  ensure_kv "$ENVF" REGENT_WORKER_REPLICAS 3
  ensure_kv "$ENVF" REGENT_WORKER_DISPATCH_CONCURRENCY 2
done
echo '=== deploy.env knobs ==='
grep -E 'MODEL_TIMEOUT|MAX_CONCURRENT|WORKER_REPL|DISPATCH_CONC|MODEL_NAME|MODEL_BASE' /opt/regent/.deploy.env || true
echo '=== reclaim expired DISPATCHING ==='
docker exec -i regent-api python - <<'PY'
from sqlalchemy import create_engine, text
from regent.config import get_settings
url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
with eng.begin() as c:
    n = c.execute(text("""
      UPDATE outbox_events
      SET status='PENDING', lease_owner=NULL, lease_expires_at=NULL, available_at=NOW()
      WHERE event_type='GenerationRunRequested'
        AND status='DISPATCHING'
        AND (lease_expires_at IS NULL OR lease_expires_at < NOW())
    """)).rowcount
    print({"reclaimed_dispatching": n})
PY
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
