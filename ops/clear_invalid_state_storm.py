"""Ack invalid_state replan-storm outbox rows and mint one clean GenerationRunRequested.

Default dry-run. Pass --execute to apply on S0.
"""

from __future__ import annotations

import argparse
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
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from regent.config import get_settings

EXECUTE = __EXECUTE__

url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
now = datetime.now(timezone.utc)
stamp = now.strftime("%Y%m%d%H%M%S")
out = {"acked": 0, "reset": 0, "fresh": 0, "candidates": 0}

with eng.begin() as c:
    storm = c.execute(
        text(
            """
            SELECT id::text
            FROM outbox_events
            WHERE event_type = 'GenerationRunRequested'
              AND status IN ('PENDING','DISPATCHING','FAILED','DEAD_LETTER')
              AND (
                payload::text ILIKE '%idempotency key scope mismatch%'
                OR payload::text ILIKE '%frozen generation plan is required%'
                OR COALESCE(last_error,'') ILIKE '%idempotency key scope mismatch%'
                OR COALESCE(last_error,'') ILIKE '%frozen generation plan is required%'
              )
            """
        )
    ).fetchall()
    out["acked"] = len(storm)

    goals = c.execute(
        text(
            """
            SELECT g.id::text AS goal_id,
                   g.app_project_id::text AS app_project_id,
                   g.correlation_id::text AS correlation_id,
                   g.version AS goal_version,
                   g.metadata->>'requirement_revision_id' AS requirement_revision_id,
                   g.metadata->>'capability_resolution_plan_id' AS capability_resolution_plan_id
            FROM goals g
            WHERE g.status = 'ACTIVE'
              AND g.created_at > NOW() - interval '12 hours'
              AND g.metadata->>'requirement_revision_id' IS NOT NULL
              AND g.metadata->>'capability_resolution_plan_id' IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM outbox_events o
                WHERE o.aggregate_id = g.id
                  AND o.event_type = 'GenerationRunRequested'
                  AND o.status IN ('PENDING','DISPATCHING')
              )
            """
        )
    ).mappings().all()
    out["candidates"] = len(goals)

    if not EXECUTE:
        print(json.dumps({"dry_run": True, **out}, ensure_ascii=False))
        raise SystemExit(0)

    if storm:
        c.execute(
            text(
                """
                UPDATE outbox_events
                SET status = 'DISPATCHED',
                    attempt = GREATEST(attempt, 1),
                    last_error = left(
                      COALESCE(last_error,'') || ' [acked:invalid_state-storm]',
                      500
                    )
                WHERE event_type = 'GenerationRunRequested'
                  AND status IN ('PENDING','DISPATCHING','FAILED','DEAD_LETTER')
                  AND (
                    payload::text ILIKE '%idempotency key scope mismatch%'
                    OR payload::text ILIKE '%frozen generation plan is required%'
                    OR COALESCE(last_error,'') ILIKE '%idempotency key scope mismatch%'
                    OR COALESCE(last_error,'') ILIKE '%frozen generation plan is required%'
                  )
                """
            )
        )

    reset = c.execute(
        text(
            """
            UPDATE goals
            SET metadata = (COALESCE(metadata, '{}'::jsonb)
                  - 'delivery_gap_kind_streak')
                || jsonb_build_object(
                     'delivery_gap_recovery_attempts', 0,
                     'delivery_gap_kind_streak', 0,
                     'storm_reset_at', to_jsonb(NOW()::text)
                   ),
                updated_at = NOW()
            WHERE status = 'ACTIVE'
              AND created_at > NOW() - interval '12 hours'
            RETURNING id::text
            """
        )
    ).fetchall()
    out["reset"] = len(reset)

    for i, g in enumerate(goals):
        idem = f"storm-clear:{g['goal_id']}:{stamp}"
        c.execute(
            text(
                """
                INSERT INTO outbox_events (
                  id, event_type, aggregate_type, aggregate_id, aggregate_version,
                  payload, status, attempt, available_at, occurred_at, correlation_id
                ) VALUES (
                  gen_random_uuid(), 'GenerationRunRequested', 'goal',
                  CAST(:goal_id AS uuid), :version, CAST(:payload AS jsonb),
                  'PENDING', 0,
                  NOW() + make_interval(secs => :delay),
                  NOW(), CAST(:corr AS uuid)
                )
                """
            ),
            {
                "goal_id": g["goal_id"],
                "version": int(g["goal_version"] or 0),
                "corr": g["correlation_id"],
                "delay": i * 15,
                "payload": json.dumps(
                    {
                        "goal_id": g["goal_id"],
                        "app_project_id": g["app_project_id"],
                        "requirement_revision_id": g["requirement_revision_id"],
                        "capability_resolution_plan_id": g[
                            "capability_resolution_plan_id"
                        ],
                        "actor": "regent-ops:storm-clear",
                        "idempotency_key": idem,
                    }
                ),
            },
        )
        out["fresh"] += 1

print(json.dumps({"executed": True, **out}, ensure_ascii=False))
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    remote = REMOTE.replace("__EXECUTE__", "True" if args.execute else "False")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/clear_invalid_state_storm.py", "w") as f:
        f.write(remote)
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/clear_invalid_state_storm.py regent-api:/tmp/clear_invalid_state_storm.py "
        "&& docker exec -w /tmp regent-api python clear_invalid_state_storm.py",
        timeout=120,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
