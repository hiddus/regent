"""Fix delivery fake-alive: zombies, dead letters, idempotency mismatch.

Default dry-run. Pass --execute to apply on S0.

Steps:
  1) Stale GENERATING runs → FAILED; requeue ACTIVE goals with lineage (new idem).
  2) Fake-alive ACTIVE@GENERATING (no open outbox/run):
       - with lineage → keep ACTIVE, enqueue fresh GenerationRunRequested
       - without lineage → mark FAILED (console-honest end state)
  3) Retryable GenerationRunRequested DEAD_LETTER/FAILED → PENDING
       (LEASE_CONFLICT / 504 / etc.), attempt reset, staggered available_at.
  4) Non-retryable idempotency scope mismatch → DISPATCHED + fresh event.

Usage:
  python ops/fix_delivery_stuck.py
  python ops/fix_delivery_stuck.py --execute
  python ops/fix_delivery_stuck.py --execute --stale-hours 1 --stagger-seconds 20
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
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

REMOTE_PY = r'''
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from regent.config import get_settings

STALE_HOURS = __STALE_HOURS__
GOAL_STALE_HOURS = __GOAL_STALE_HOURS__
STAGGER_SECONDS = __STAGGER_SECONDS__
EXECUTE = __EXECUTE__

url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)

now = datetime.now(timezone.utc)
run_cutoff = now - timedelta(hours=STALE_HOURS)
goal_cutoff = now - timedelta(hours=GOAL_STALE_HOURS)
stamp = now.strftime("%Y%m%d%H%M%S")

probe = {
    "stale_generating_runs": 0,
    "fake_alive_with_lineage": 0,
    "fake_alive_no_lineage": 0,
    "retryable_dead": 0,
    "idem_mismatch": 0,
    "runs_failed": 0,
    "goals_failed": 0,
    "events_requeued": 0,
    "fresh_events": 0,
    "mismatch_acked": 0,
}


def enqueue_gen_run(c, *, goal, idem: str, actor: str, delay_s: int = 0) -> bool:
    """Insert GenerationRunRequested if same idem not already present."""
    exists = c.execute(
        text("""
        SELECT 1 FROM outbox_events
        WHERE event_type = 'GenerationRunRequested'
          AND aggregate_id = CAST(:goal_id AS uuid)
          AND payload->>'idempotency_key' = :idem
        LIMIT 1
        """),
        {"goal_id": goal["goal_id"], "idem": idem},
    ).first()
    if exists is not None:
        return False
    avail = now + timedelta(seconds=max(0, delay_s))
    c.execute(
        text("""
        INSERT INTO outbox_events (
          id, event_type, aggregate_type, aggregate_id, aggregate_version,
          payload, status, attempt, available_at, occurred_at, correlation_id
        ) VALUES (
          gen_random_uuid(),
          'GenerationRunRequested',
          'goal',
          CAST(:goal_id AS uuid),
          :version,
          CAST(:payload AS jsonb),
          'PENDING',
          0,
          :avail,
          NOW(),
          CAST(:corr AS uuid)
        )
        """),
        {
            "goal_id": goal["goal_id"],
            "version": int(goal["goal_version"] or 0),
            "corr": goal["correlation_id"],
            "avail": avail,
            "payload": json.dumps({
                "goal_id": goal["goal_id"],
                "app_project_id": goal["app_project_id"],
                "requirement_revision_id": goal["requirement_revision_id"],
                "capability_resolution_plan_id": goal["capability_resolution_plan_id"],
                "actor": actor,
                "idempotency_key": idem,
            }),
        },
    )
    return True


with eng.begin() as c:
    stale_runs = c.execute(
        text("""
        SELECT r.id::text AS run_id,
               g.id::text AS goal_id,
               g.status AS goal_status,
               g.app_project_id::text AS app_project_id,
               g.correlation_id::text AS correlation_id,
               g.version AS goal_version,
               g.metadata->>'requirement_revision_id' AS requirement_revision_id,
               g.metadata->>'capability_resolution_plan_id' AS capability_resolution_plan_id,
               r.updated_at
        FROM generation_runs r
        JOIN generation_plans p ON p.id = r.plan_id
        JOIN requirement_revisions rr ON rr.id = p.requirement_revision_id
        JOIN goals g ON g.id = rr.goal_id
        WHERE r.status = 'GENERATING'
          AND r.updated_at < :cutoff
        ORDER BY r.updated_at ASC
        LIMIT 300
        """),
        {"cutoff": run_cutoff},
    ).mappings().all()
    probe["stale_generating_runs"] = len(stale_runs)

    fake_alive = c.execute(
        text("""
        SELECT g.id::text AS goal_id,
               g.status AS goal_status,
               g.app_project_id::text AS app_project_id,
               g.correlation_id::text AS correlation_id,
               g.version AS goal_version,
               g.metadata->>'requirement_revision_id' AS requirement_revision_id,
               g.metadata->>'capability_resolution_plan_id' AS capability_resolution_plan_id,
               g.updated_at
        FROM goals g
        WHERE g.status = 'ACTIVE'
          AND COALESCE(g.metadata->>'execution_stage', '') = 'GENERATING'
          AND g.updated_at < :cutoff
          AND NOT EXISTS (
            SELECT 1 FROM generation_runs r
            JOIN generation_plans p ON p.id = r.plan_id
            JOIN requirement_revisions rr ON rr.id = p.requirement_revision_id
            WHERE rr.goal_id = g.id AND r.status = 'GENERATING'
          )
          AND NOT EXISTS (
            SELECT 1 FROM outbox_events o
            WHERE o.aggregate_id = g.id
              AND o.event_type = 'GenerationRunRequested'
              AND o.status IN ('PENDING', 'DISPATCHING', 'FAILED')
          )
        ORDER BY g.updated_at ASC
        LIMIT 300
        """),
        {"cutoff": goal_cutoff},
    ).mappings().all()

    with_lineage = [
        r for r in fake_alive
        if r["app_project_id"] and r["requirement_revision_id"] and r["capability_resolution_plan_id"]
    ]
    no_lineage = [r for r in fake_alive if r not in with_lineage]
    # Prefer replaying existing DEAD_LETTER over minting a second event.
    dead_goal_ids = {
        str(x)
        for x in c.execute(
            text("""
            SELECT DISTINCT aggregate_id::text
            FROM outbox_events
            WHERE event_type = 'GenerationRunRequested'
              AND status IN ('DEAD_LETTER', 'FAILED')
            """)
        ).scalars().all()
    }
    with_lineage_need_fresh = [r for r in with_lineage if r["goal_id"] not in dead_goal_ids]
    probe["fake_alive_with_lineage"] = len(with_lineage)
    probe["fake_alive_need_fresh"] = len(with_lineage_need_fresh)
    probe["fake_alive_no_lineage"] = len(no_lineage)

    retryable = c.execute(
        text("""
        SELECT id::text AS event_id, last_error, attempt
        FROM outbox_events
        WHERE event_type = 'GenerationRunRequested'
          AND status IN ('DEAD_LETTER', 'FAILED')
          AND (
            last_error IS NULL
            OR last_error NOT LIKE '[non-retryable]%'
          )
        ORDER BY available_at ASC
        LIMIT 500
        """),
    ).mappings().all()
    probe["retryable_dead"] = len(retryable)

    mismatches = c.execute(
        text("""
        SELECT o.id::text AS event_id,
               o.aggregate_id::text AS goal_id,
               o.payload,
               g.app_project_id::text AS app_project_id,
               g.correlation_id::text AS correlation_id,
               g.version AS goal_version,
               g.status AS goal_status,
               COALESCE(
                 o.payload->>'requirement_revision_id',
                 g.metadata->>'requirement_revision_id'
               ) AS requirement_revision_id,
               COALESCE(
                 o.payload->>'capability_resolution_plan_id',
                 g.metadata->>'capability_resolution_plan_id'
               ) AS capability_resolution_plan_id
        FROM outbox_events o
        JOIN goals g ON g.id = o.aggregate_id
        WHERE o.event_type = 'GenerationRunRequested'
          AND o.status IN ('DEAD_LETTER', 'FAILED')
          AND o.last_error LIKE '%idempotency key scope mismatch%'
        LIMIT 100
        """),
    ).mappings().all()
    probe["idem_mismatch"] = len(mismatches)

    # Exclude mismatch rows from blind retryable requeue (need fresh idem keys).
    mismatch_ids = {r["event_id"] for r in mismatches}
    retryable = [r for r in retryable if r["event_id"] not in mismatch_ids]
    probe["retryable_dead"] = len(retryable)

    if not EXECUTE:
        print(json.dumps({"dry_run": True, **probe}, default=str, ensure_ascii=False))
        raise SystemExit(0)

    # --- 1) Fail stale runs + requeue ---
    for i, row in enumerate(stale_runs):
        c.execute(
            text("""
            UPDATE generation_runs
            SET status = 'FAILED',
                failure_code = 'ZOMBIE_STALE_GENERATING',
                updated_at = NOW()
            WHERE id = CAST(:id AS uuid) AND status = 'GENERATING'
            """),
            {"id": row["run_id"]},
        )
        probe["runs_failed"] += 1
        # Only mint a fresh event when there is no dead/failed outbox to replay.
        if (
            row["goal_status"] == "ACTIVE"
            and row["requirement_revision_id"]
            and row["capability_resolution_plan_id"]
            and row["app_project_id"]
            and row["goal_id"] not in dead_goal_ids
        ):
            idem = f"stuck-fix:stale-run:{row['goal_id']}:{stamp}"
            if enqueue_gen_run(
                c,
                goal=row,
                idem=idem,
                actor="regent-ops:fix-delivery-stuck",
                delay_s=i * STAGGER_SECONDS,
            ):
                probe["fresh_events"] += 1

    # --- 2) Fake-alive without dead letter: fresh event; no lineage → FAILED ---
    for i, row in enumerate(with_lineage_need_fresh):
        idem = f"stuck-fix:fake-alive:{row['goal_id']}:{stamp}"
        if enqueue_gen_run(
            c,
            goal=row,
            idem=idem,
            actor="regent-ops:fix-delivery-stuck",
            delay_s=(len(stale_runs) + i) * STAGGER_SECONDS,
        ):
            probe["fresh_events"] += 1
            c.execute(
                text("""
                UPDATE goals
                SET updated_at = NOW(),
                    metadata = jsonb_set(
                      COALESCE(metadata, '{}'::jsonb),
                      '{stuck_fix_reenqueued_at}',
                      to_jsonb(NOW()::text)
                    )
                WHERE id = CAST(:id AS uuid)
                """),
                {"id": row["goal_id"]},
            )

    for row in no_lineage:
        c.execute(
            text("""
            UPDATE goals
            SET status = 'FAILED',
                version = version + 1,
                updated_at = NOW(),
                metadata = jsonb_set(
                  jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{execution_stage}',
                    '"FAILED"'
                  ),
                  '{zombie_cleared}',
                  to_jsonb(NOW()::text)
                )
            WHERE id = CAST(:id AS uuid) AND status = 'ACTIVE'
            """),
            {"id": row["goal_id"]},
        )
        probe["goals_failed"] += 1

    # --- 3) Retryable dead letters → PENDING staggered ---
    for i, row in enumerate(retryable):
        avail = now + timedelta(seconds=i * STAGGER_SECONDS)
        c.execute(
            text("""
            UPDATE outbox_events
            SET status = 'PENDING',
                lease_owner = NULL,
                lease_expires_at = NULL,
                available_at = :avail,
                attempt = 0,
                last_error = :note
            WHERE id = CAST(:id AS uuid)
              AND status IN ('DEAD_LETTER', 'FAILED')
            """),
            {
                "id": row["event_id"],
                "avail": avail,
                "note": f"requeued by fix-delivery-stuck at {now.isoformat()}",
            },
        )
        probe["events_requeued"] += 1

    # --- 4) Idempotency mismatch → ack + fresh key ---
    for i, row in enumerate(mismatches):
        c.execute(
            text("""
            UPDATE outbox_events
            SET status = 'DISPATCHED',
                lease_owner = NULL,
                lease_expires_at = NULL,
                last_error = :note
            WHERE id = CAST(:id AS uuid)
              AND status IN ('DEAD_LETTER', 'FAILED')
            """),
            {
                "id": row["event_id"],
                "note": f"acked scope-mismatch; fresh event by fix-delivery-stuck at {now.isoformat()}",
            },
        )
        probe["mismatch_acked"] += 1
        if (
            row["goal_status"] in {"ACTIVE", "FAILED", "EXHAUSTED", "BLOCKED"}
            and row["app_project_id"]
            and row["requirement_revision_id"]
            and row["capability_resolution_plan_id"]
        ):
            idem = f"stuck-fix:idem-mismatch:{row['goal_id']}:{stamp}"
            if enqueue_gen_run(
                c,
                goal=row,
                idem=idem,
                actor="regent-ops:fix-delivery-stuck",
                delay_s=i * STAGGER_SECONDS,
            ):
                probe["fresh_events"] += 1

print(json.dumps({"executed": True, **probe}, default=str, ensure_ascii=False))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stale-hours", type=float, default=1.0)
    parser.add_argument("--goal-stale-hours", type=float, default=1.0)
    parser.add_argument("--stagger-seconds", type=int, default=20)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    remote = (
        REMOTE_PY.replace("__STALE_HOURS__", str(float(args.stale_hours)))
        .replace("__GOAL_STALE_HOURS__", str(float(args.goal_stale_hours)))
        .replace("__STAGGER_SECONDS__", str(int(args.stagger_seconds)))
        .replace("__EXECUTE__", "True" if args.execute else "False")
    )

    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=CFG.get("LOGIN_USER") or "root", password=PASSWORD, timeout=30)
    cmd = "docker exec -i regent-api python - <<'PY'\n" + remote + "\nPY"
    _, o, e = ssh.exec_command(cmd, timeout=300)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    print(out)
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
