"""Quick worker log + queue health check on S0."""

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
echo '=== outbox snapshot ==='
docker exec -i regent-api python - <<'PY'
import json
from sqlalchemy import create_engine, text
from regent.config import get_settings
url = get_settings().database_url
sync = url if '+psycopg' in url else url.replace('postgresql://','postgresql+psycopg://',1)
eng = create_engine(sync)
with eng.connect() as c:
    ob = dict(c.execute(text("SELECT status, COUNT(*) FROM outbox_events WHERE event_type='GenerationRunRequested' GROUP BY 1")).all())
    gen = dict(c.execute(text("SELECT status, COUNT(*) FROM generation_runs WHERE status IN ('GENERATING','REQUESTED') GROUP BY 1")).all())
    lease = c.execute(text("SELECT COUNT(*) FROM outbox_events WHERE event_type='GenerationRunRequested' AND status IN ('FAILED','DEAD_LETTER') AND last_error LIKE '%LEASE_CONFLICT%' AND available_at > NOW() - interval '10 minutes'")).scalar()
    gw = c.execute(text("SELECT COUNT(*) FROM outbox_events WHERE event_type='GenerationRunRequested' AND status IN ('FAILED','DEAD_LETTER') AND last_error LIKE '%504%' AND available_at > NOW() - interval '10 minutes'")).scalar()
print(json.dumps({'outbox':ob,'active_runs':gen,'lease_10m':int(lease or 0),'gw504_10m':int(gw or 0)}))
PY
echo '=== worker tails ==='
for w in regent-worker regent-worker-2 regent-worker-3; do
  echo "-- $w --"
  docker logs --tail 8 "$w" 2>&1 | tail -8 || true
done
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
    _, o, e = ssh.exec_command(CMD, timeout=120)
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
