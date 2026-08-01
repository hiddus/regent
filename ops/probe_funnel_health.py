"""One-shot S0 funnel health after unblock deploy."""

from __future__ import annotations

import json
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
from regent.application.delivery_success_policy import effective_max_concurrent_generating
from regent.application.planned_path_policy import expand_planned_paths

s = get_settings()
url = s.database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
with eng.connect() as c:
    outbox = dict(
        c.execute(
            text(
                "SELECT status, COUNT(*) FROM outbox_events "
                "WHERE event_type='GenerationRunRequested' GROUP BY 1"
            )
        ).all()
    )
    cannot_mark = c.execute(
        text(
            "SELECT COUNT(*) FROM outbox_events WHERE last_error ILIKE "
            "'%cannot mark FAILED_TERMINAL%' "
            "AND status IN ('FAILED','DEAD_LETTER','PENDING','DISPATCHING')"
        )
    ).scalar()
    path_outside = c.execute(
        text(
            "SELECT COUNT(*) FROM outbox_events WHERE last_error ILIKE "
            "'%outside frozen plan%' "
            "AND status IN ('FAILED','DEAD_LETTER','PENDING','DISPATCHING')"
        )
    ).scalar()
    active_live = c.execute(
        text(
            """
            SELECT COUNT(*) FROM goals g
            WHERE g.status='ACTIVE'
              AND COALESCE(g.metadata->'live_action'->>'updated_at','') <> ''
              AND (g.metadata->'live_action'->>'updated_at')::timestamptz
                    > NOW() - INTERVAL '15 minutes'
            """
        )
    ).scalar()
    stale_2h = c.execute(
        text(
            """
            SELECT COUNT(*) FROM goals g
            WHERE g.status='ACTIVE'
              AND (
                g.metadata->'live_action'->>'updated_at' IS NULL
                OR (g.metadata->'live_action'->>'updated_at')::timestamptz
                     < NOW() - INTERVAL '2 hours'
              )
            """
        )
    ).scalar()
    active = c.execute(text("SELECT COUNT(*) FROM goals WHERE status='ACTIVE'")).scalar()
    generating = c.execute(
        text("SELECT COUNT(*) FROM generation_runs WHERE status='GENERATING'")
    ).scalar()
    recent_dl = c.execute(
        text(
            """
            SELECT COALESCE(left(last_error, 120), ''), COUNT(*)
            FROM outbox_events
            WHERE event_type='GenerationRunRequested'
              AND status IN ('DEAD_LETTER','FAILED')
              AND occurred_at > NOW() - INTERVAL '30 minutes'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
            """
        )
    ).all()
print(
    json.dumps(
        {
            "imports_ok": True,
            "expand_sample": expand_planned_paths(["src/app.py"])[:4],
            "cap": effective_max_concurrent_generating(s),
            "replicas": s.worker_replicas,
            "dispatch": s.worker_dispatch_concurrency,
            "outbox_GenRun": outbox,
            "cannot_mark_open": int(cannot_mark or 0),
            "path_outside_open": int(path_outside or 0),
            "active_goals": int(active or 0),
            "active_live_15m": int(active_live or 0),
            "stale_2h": int(stale_2h or 0),
            "generating_runs": int(generating or 0),
            "recent_dead_failed_30m": [{"e": e, "n": n} for e, n in recent_dl],
        },
        ensure_ascii=False,
        indent=2,
    )
)
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
    _, o, e = ssh.exec_command(
        "docker exec -i regent-api python - <<'PY'\n" + REMOTE + "\nPY",
        timeout=90,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
