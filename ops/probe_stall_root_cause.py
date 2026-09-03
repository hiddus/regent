"""Diagnose the 2026-08-11 production stall: mass GenerationRunRequested dead letters
plus 83/84 active goals parked in DELIVERY_SOFT_PAUSE."""

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

out = {}
with eng.connect() as c:
    cols = {r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='outbox_events'"
    )).all()}
    out["outbox_columns"] = sorted(cols)

    err = "last_error" if "last_error" in cols else ("error" if "error" in cols else None)
    if err:
        out["dead_letter_errors"] = [
            {"event_type": t, "n": n, "sample": (e or "")[:400]}
            for t, n, e in c.execute(text(f"""
                SELECT event_type, COUNT(*) AS n, MIN({err}) AS e
                FROM outbox_events
                WHERE status = 'DEAD_LETTER'
                GROUP BY 1 ORDER BY 2 DESC
            """)).all()
        ]
        out["dead_letter_distinct_errors"] = [
            {"n": n, "err": (e or "")[:300]}
            for n, e in c.execute(text(f"""
                SELECT COUNT(*) AS n, left(coalesce({err},''), 300) AS e
                FROM outbox_events
                WHERE status = 'DEAD_LETTER' AND event_type = 'GenerationRunRequested'
                GROUP BY 2 ORDER BY 1 DESC LIMIT 10
            """)).all()
        ]

    out["dead_letter_time_span"] = [
        dict(r) for r in c.execute(text("""
            SELECT event_type,
                   MIN(occurred_at)::text AS first_seen,
                   MAX(occurred_at)::text AS last_seen,
                   MAX(attempt) AS max_attempt,
                   COUNT(*) AS n
            FROM outbox_events WHERE status='DEAD_LETTER'
            GROUP BY 1 ORDER BY 5 DESC
        """)).mappings().all()
    ]

    gcols = {r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='goals'"
    )).all()}
    out["has_goal_metadata"] = "metadata" in gcols

    out["soft_pause_reasons"] = [
        dict(r) for r in c.execute(text("""
            SELECT COALESCE(g.metadata->>'delivery_soft_pause_reason',
                            g.metadata->>'soft_pause_reason',
                            g.metadata->>'pause_reason', '(none)') AS reason,
                   COUNT(*) AS n,
                   MIN(g.updated_at)::text AS oldest,
                   MAX(g.updated_at)::text AS newest
            FROM goals g
            WHERE g.status='ACTIVE'
              AND COALESCE(g.metadata->>'execution_stage','')='DELIVERY_SOFT_PAUSE'
            GROUP BY 1 ORDER BY 2 DESC
        """)).mappings().all()
    ]

    out["soft_pause_metadata_keys"] = [
        dict(r) for r in c.execute(text("""
            SELECT k AS key, COUNT(*) AS n
            FROM goals g, jsonb_object_keys(g.metadata) AS k
            WHERE g.status='ACTIVE'
              AND COALESCE(g.metadata->>'execution_stage','')='DELIVERY_SOFT_PAUSE'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 40
        """)).mappings().all()
    ]

    out["soft_pause_sample"] = [
        dict(r) for r in c.execute(text("""
            SELECT g.id::text, g.updated_at::text,
                   left(coalesce(g.original_input,''),70) AS input,
                   g.metadata::text AS meta
            FROM goals g
            WHERE g.status='ACTIVE'
              AND COALESCE(g.metadata->>'execution_stage','')='DELIVERY_SOFT_PAUSE'
            ORDER BY g.updated_at DESC LIMIT 3
        """)).mappings().all()
    ]

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
'''


def main() -> int:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=60,
        banner_timeout=60,
        auth_timeout=120,
        look_for_keys=False,
        allow_agent=False,
    )
    cmd = "docker exec -i regent-api python - <<'PY'\n" + REMOTE + "\nPY"
    _, o, e = ssh.exec_command(cmd, timeout=180)
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
