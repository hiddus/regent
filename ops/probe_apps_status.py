"""List recent app / goal status on S0 for operator triage."""

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
    summary = c.execute(text("""
      SELECT g.status, COALESCE(g.metadata->>'execution_stage','') AS stage, COUNT(*)
      FROM goals g
      WHERE g.created_at > NOW() - interval '7 days'
      GROUP BY 1, 2
      ORDER BY 3 DESC
    """)).all()
    apps = c.execute(text("""
      SELECT p.id::text AS project_id,
             p.name,
             p.status AS project_status,
             g.id::text AS goal_id,
             g.status AS goal_status,
             COALESCE(g.metadata->>'execution_stage','') AS stage,
             CASE
               WHEN EXISTS (
                 SELECT 1 FROM generation_runs r
                 JOIN generation_plans gp ON gp.id = r.plan_id
                 JOIN requirement_revisions rr ON rr.id = gp.requirement_revision_id
                 WHERE rr.goal_id = g.id AND r.status = 'GENERATING'
               ) THEN 'calling_model'
               WHEN EXISTS (
                 SELECT 1 FROM outbox_events o
                 WHERE o.aggregate_id = g.id
                   AND o.event_type = 'GenerationRunRequested'
                   AND o.status IN ('PENDING','DISPATCHING','FAILED')
               ) THEN 'queued'
               WHEN g.status IN ('FAILED','EXHAUSTED','BLOCKED') THEN 'needs_continue'
               WHEN g.status = 'WAITING_HUMAN' THEN 'waiting_human'
               WHEN COALESCE(g.metadata->>'execution_stage','') = 'GENERATING'
                    AND g.status = 'ACTIVE' THEN 'stalled'
               ELSE 'idle'
             END AS generation_progress,
             COALESCE(g.metadata->>'last_preview_endpoint','') AS preview,
             g.updated_at,
             left(COALESCE(g.original_input,''), 60) AS input
      FROM goals g
      JOIN app_projects p ON p.id = g.app_project_id
      WHERE g.updated_at > NOW() - interval '48 hours'
      ORDER BY g.updated_at DESC
      LIMIT 40
    """)).mappings().all()
    queue = c.execute(text("""
      SELECT status, COUNT(*) FROM outbox_events
      WHERE event_type='GenerationRunRequested'
      GROUP BY 1 ORDER BY 1
    """)).all()
    runs = c.execute(text("""
      SELECT status, COUNT(*) FROM generation_runs
      WHERE status IN ('GENERATING','REQUESTED','FAILED','COMPLETED')
      GROUP BY 1
    """)).all()
print(json.dumps({
  "goal_status_x_stage_7d": [{"status": s, "stage": st, "n": n} for s, st, n in summary],
  "outbox_GenRun": {s: n for s, n in queue},
  "generation_runs": {s: n for s, n in runs},
  "recent_apps": [dict(r) for r in apps],
}, ensure_ascii=False, indent=2, default=str))
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
