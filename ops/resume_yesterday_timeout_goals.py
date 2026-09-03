"""Resume yesterday-created apps that timed out / were invalidated.

Scope (Asia/Shanghai calendar day 2026-07-31):
  - WAITING_HUMAN → HUMAN_RESOLVED + delivery-gap resume (or requeue generation)
  - FAILED / EXHAUSTED / BLOCKED → REPLAN + GenerationRunRequested
  - Skip CANCELLED (explicit cancel) and already-ACTIVE GENERATING

Usage:
  python ops/resume_yesterday_timeout_goals.py           # dry-run
  python ops/resume_yesterday_timeout_goals.py --execute
"""

from __future__ import annotations

import argparse
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
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

REMOTE = r'''
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from regent.config import get_settings

EXECUTE = __EXECUTE__
# Asia/Shanghai 2026-07-31
START = "2026-07-30 16:00:00+00"
END = "2026-07-31 16:00:00+00"
ACTOR = "regent-ops:resume-timeout"

url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)

cand_sql = text("""
SELECT g.id::text AS goal_id,
       g.app_project_id::text AS project_id,
       p.name AS project_name,
       g.status,
       g.version,
       g.correlation_id::text AS correlation_id,
       g.metadata->>'execution_stage' AS stage,
       g.metadata->>'requirement_revision_id' AS req_id,
       g.metadata->>'capability_resolution_plan_id' AS plan_id,
       COALESCE((g.metadata->>'delivery_gap_recovery_attempts')::int, 0) AS gap_attempts,
       (
         SELECT COUNT(*) FROM human_tasks ht
         WHERE ht.goal_id = g.id AND ht.status = 'TIMED_OUT'
       ) AS timed_out_n
FROM goals g
LEFT JOIN app_projects p ON p.id = g.app_project_id
WHERE g.created_at >= CAST(:start AS timestamptz)
  AND g.created_at <  CAST(:end AS timestamptz)
  AND g.status IN ('WAITING_HUMAN', 'FAILED', 'EXHAUSTED', 'BLOCKED')
  AND g.app_project_id IS NOT NULL
ORDER BY g.created_at
""")

results = {
    "selected": 0,
    "resumed": 0,
    "skipped": [],
    "errors": [],
    "items": [],
}

with eng.connect() as conn:
    rows = [dict(r._mapping) for r in conn.execute(cand_sql, {"start": START, "end": END})]
results["selected"] = len(rows)

if not EXECUTE:
    results["dry_run"] = True
    results["items"] = [
        {
            "goal_id": r["goal_id"],
            "name": r["project_name"],
            "status": r["status"],
            "stage": r["stage"],
            "timed_out_n": r["timed_out_n"],
            "has_lineage": bool(r["req_id"] and r["plan_id"]),
        }
        for r in rows
    ]
    print(json.dumps(results, ensure_ascii=False, default=str))
    raise SystemExit(0)

now = datetime.now(timezone.utc)
for r in rows:
    goal_id = r["goal_id"]
    project_id = r["project_id"]
    status = r["status"]
    version = int(r["version"] or 0)
    corr = r["correlation_id"]
    req_id = r["req_id"]
    plan_id = r["plan_id"]
    name = r["project_name"] or goal_id

    if not req_id or not plan_id:
        results["skipped"].append({"goal_id": goal_id, "name": name, "reason": "missing_lineage"})
        continue

    try:
        with eng.begin() as conn:
            conn.execute(
                text("""
                UPDATE human_tasks
                SET status = 'COMPLETED',
                    assigned_to = :actor,
                    response = jsonb_build_object(
                      'decision', 'APPROVE',
                      'approved', true,
                      'feedback', 'ops resume after timeout',
                      'reason', 'continue_after_timeout'
                    ),
                    completed_at = NOW()
                WHERE goal_id = CAST(:goal_id AS uuid)
                  AND status = 'TIMED_OUT'
                """),
                {"goal_id": goal_id, "actor": ACTOR},
            )

            if status == "WAITING_HUMAN":
                action = "HUMAN_RESOLVED"
            else:
                action = "REPLAN"
            new_status = "ACTIVE"

            upd = conn.execute(
                text("""
                UPDATE goals
                SET status = :new_status,
                    version = version + 1,
                    updated_at = NOW(),
                    metadata = jsonb_set(
                      jsonb_set(
                        jsonb_set(
                          jsonb_set(
                            COALESCE(metadata, '{}'::jsonb),
                            '{execution_stage}', '"GENERATING"'
                          ),
                          '{delivery_gap_recovery_attempts}', '0'
                        ),
                        '{delivery_gap_kind_streak}', '0'
                      ),
                      '{timeout_resume_at}', to_jsonb(NOW()::text)
                    )
                    - 'awaiting_human_intervention'
                    - 'awaiting_verification'
                    - 'termination'
                    - 'pending_delivery_gap_human'
                    - 'zombie_cleared'
                    - 'halt'
                WHERE id = CAST(:goal_id AS uuid)
                  AND status = :old_status
                  AND version = :version
                """),
                {
                    "goal_id": goal_id,
                    "new_status": new_status,
                    "old_status": status,
                    "version": version,
                },
            )
            if upd.rowcount != 1:
                results["skipped"].append({
                    "goal_id": goal_id,
                    "name": name,
                    "reason": f"version_or_status_mismatch status={status} v={version}",
                })
                raise RuntimeError("skip_mismatch")

            new_version = version + 1

            conn.execute(
                text("""
                INSERT INTO audit_records (
                  id, aggregate_type, aggregate_id, aggregate_version,
                  action, actor, payload, correlation_id, occurred_at
                ) VALUES (
                  gen_random_uuid(), 'goal', CAST(:goal_id AS uuid), :ver,
                  :action, :actor,
                  CAST(:payload AS jsonb),
                  CAST(:corr AS uuid), NOW()
                )
                """),
                {
                    "goal_id": goal_id,
                    "ver": new_version,
                    "action": f"OPS_{action}_TIMEOUT_RESUME",
                    "actor": ACTOR,
                    "corr": corr,
                    "payload": json.dumps({
                        "from_status": status,
                        "to_status": new_status,
                        "project_name": name,
                        "reason": "resume yesterday timeout-invalidated apps",
                    }),
                },
            )

            idem = f"timeout-resume:{goal_id}:{now.strftime('%Y%m%d%H%M')}"
            exists = conn.execute(
                text("""
                SELECT 1 FROM outbox_events
                WHERE event_type = 'GenerationRunRequested'
                  AND aggregate_id = CAST(:goal_id AS uuid)
                  AND payload->>'idempotency_key' = :idem
                LIMIT 1
                """),
                {"goal_id": goal_id, "idem": idem},
            ).first()
            if exists is None:
                conn.execute(
                    text("""
                    INSERT INTO outbox_events (
                      id, event_type, aggregate_type, aggregate_id, aggregate_version,
                      payload, status, attempt, available_at, occurred_at, correlation_id
                    ) VALUES (
                      gen_random_uuid(),
                      'GenerationRunRequested',
                      'goal',
                      CAST(:goal_id AS uuid),
                      :ver,
                      CAST(:payload AS jsonb),
                      'PENDING',
                      0,
                      NOW(),
                      NOW(),
                      CAST(:corr AS uuid)
                    )
                    """),
                    {
                        "goal_id": goal_id,
                        "ver": new_version,
                        "corr": corr,
                        "payload": json.dumps({
                            "goal_id": goal_id,
                            "app_project_id": project_id,
                            "requirement_revision_id": req_id,
                            "capability_resolution_plan_id": plan_id,
                            "actor": ACTOR,
                            "idempotency_key": idem,
                        }),
                    },
                )

            conv = conn.execute(
                text("""
                SELECT id FROM conversations
                WHERE app_project_id = CAST(:pid AS uuid)
                ORDER BY created_at DESC LIMIT 1
                """),
                {"pid": project_id},
            ).first()
            if conv is not None:
                ordinal = conn.execute(
                    text("""
                    SELECT COALESCE(MAX(ordinal), 0) + 1
                    FROM conversation_messages WHERE conversation_id = :cid
                    """),
                    {"cid": conv[0]},
                ).scalar()
                conn.execute(
                    text("""
                    INSERT INTO conversation_messages (
                      id, conversation_id, ordinal, role, message_type, content,
                      metadata_json, created_by, created_at
                    ) VALUES (
                      gen_random_uuid(), :cid, :ord, 'ASSISTANT',
                      'TIMEOUT_RESUME',
                      :content,
                      CAST(:meta AS jsonb),
                      :actor,
                      NOW()
                    )
                    """),
                    {
                        "cid": conv[0],
                        "ord": int(ordinal or 1),
                        "content": "已从超时失效状态恢复执行，正在重新生成。",
                        "actor": ACTOR,
                        "meta": json.dumps({
                            "goal_id": goal_id,
                            "ops": True,
                            "from_status": status,
                        }),
                    },
                )

        results["resumed"] += 1
        results["items"].append({
            "goal_id": goal_id,
            "name": name,
            "from": status,
            "action": action,
        })
    except Exception as exc:  # noqa: BLE001
        if str(exc) == "skip_mismatch":
            continue
        results["errors"].append({"goal_id": goal_id, "name": name, "error": str(exc)[:300]})

print(json.dumps(results, ensure_ascii=False, default=str))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")

    remote = REMOTE.replace("__EXECUTE__", "True" if args.execute else "False")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=CFG.get("LOGIN_USER") or "root", password=PASSWORD, timeout=30)
    cmd = "docker exec -i regent-api python - <<'PY'\n" + remote + "\nPY"
    _, o, e = ssh.exec_command(cmd, timeout=180)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    print(out)
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
