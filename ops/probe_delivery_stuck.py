"""Probe fake-alive / outbox / generation_runs on S0."""

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
import json
from sqlalchemy import create_engine, text
from regent.config import get_settings

url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
with eng.connect() as c:
    ob = c.execute(text("""
      SELECT status, COUNT(*) FROM outbox_events
      WHERE event_type='GenerationRunRequested'
      GROUP BY 1 ORDER BY 1
    """)).all()
    errs = c.execute(text("""
      SELECT COALESCE(left(last_error, 140), '') AS e, COUNT(*) AS n
      FROM outbox_events
      WHERE event_type='GenerationRunRequested'
        AND status IN ('DEAD_LETTER', 'FAILED')
      GROUP BY 1 ORDER BY 2 DESC LIMIT 25
    """)).all()
    gen = c.execute(text("""
      SELECT status, COUNT(*) FROM generation_runs GROUP BY 1
    """)).all()
    fake = c.execute(text("""
      SELECT COUNT(*) FROM goals g
      WHERE g.status='ACTIVE'
        AND COALESCE(g.metadata->>'execution_stage', '') = 'GENERATING'
        AND NOT EXISTS (
          SELECT 1 FROM generation_runs r
          JOIN generation_plans p ON p.id = r.plan_id
          JOIN requirement_revisions rr ON rr.id = p.requirement_revision_id
          WHERE rr.goal_id = g.id AND r.status = 'GENERATING')
        AND NOT EXISTS (
          SELECT 1 FROM outbox_events o
          WHERE o.aggregate_id = g.id
            AND o.event_type = 'GenerationRunRequested'
            AND o.status IN ('PENDING', 'DISPATCHING', 'FAILED'))
    """)).scalar()
    active_gen = c.execute(text("""
      SELECT COUNT(*) FROM goals
      WHERE status='ACTIVE'
        AND COALESCE(metadata->>'execution_stage', '') = 'GENERATING'
    """)).scalar()
    pending = c.execute(text("""
      SELECT COUNT(*) FROM outbox_events
      WHERE event_type='GenerationRunRequested'
        AND status IN ('PENDING', 'DISPATCHING')
    """)).scalar()
print(json.dumps({
  "outbox_GenRun": {s: n for s, n in ob},
  "dead_failed_errors": [{"e": e, "n": n} for e, n in errs],
  "generation_runs": {s: n for s, n in gen},
  "active_generating": int(active_gen or 0),
  "fake_alive": int(fake or 0),
  "pending_or_dispatching": int(pending or 0),
}, ensure_ascii=False, indent=2))
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
    cmd = "docker exec -i regent-api python - <<'PY'\n" + REMOTE + "\nPY"
    _, o, e = ssh.exec_command(cmd, timeout=120)
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
