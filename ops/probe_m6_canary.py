"""Probe M6 agentic canary window on S0 (read-only observation).

Usage:
  python -B ops/probe_m6_canary.py
  python -B ops/probe_m6_canary.py --hours 24
  python -B ops/probe_m6_canary.py --hours 168 --out docs/m6-canary-t0.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}


REMOTE = r'''
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from regent.config import get_settings

HOURS = __HOURS__

s = get_settings()
url = s.database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
since = datetime.now(timezone.utc) - timedelta(hours=HOURS)

report = {
    "record_type": "M6CanaryProbe",
    "probed_at": datetime.now(timezone.utc).isoformat(),
    "window_hours": HOURS,
    "since": since.isoformat(),
    "settings": {
        "generation_strategy": s.generation_strategy,
        "canary_percent": int(s.generation_strategy_canary_percent),
        "canary_gate": bool(s.generation_strategy_canary_gate),
        "canary_variant": s.generation_strategy_canary_variant,
        "kill_switch": bool(s.generation_strategy_kill_switch),
        "fallback": s.generation_strategy_fallback,
        "model": s.model_name,
    },
}

with eng.connect() as c:
    # Strategy split by generator_ref in window
    rows = list(c.execute(text("""
        SELECT p.contract_json->>'generator_ref' AS ref, count(*) AS n
        FROM generation_plans p
        WHERE p.created_at >= :since
          AND p.contract_json->>'generator_ref' IN (
            'artifact-backed-code-generator-v1',
            'agentic-generation-v1'
          )
        GROUP BY 1
        ORDER BY 1
    """), {"since": since}))
    split = {r[0]: int(r[1]) for r in rows}
    ab = split.get("artifact-backed-code-generator-v1", 0)
    ag = split.get("agentic-generation-v1", 0)
    total = ab + ag
    report["strategy_split"] = {
        "artifact_backed": ab,
        "agentic": ag,
        "total": total,
        "agentic_share": (ag / total) if total else None,
    }

    # Distinct goals touched in window by arm (first plan)
    goal_rows = list(c.execute(text("""
        WITH first_plan AS (
          SELECT DISTINCT ON (rr.goal_id)
            rr.goal_id,
            p.contract_json->>'generator_ref' AS ref
          FROM generation_plans p
          JOIN requirement_revisions rr ON rr.id = p.requirement_revision_id
          WHERE p.created_at >= :since
            AND p.contract_json->>'generator_ref' IN (
              'artifact-backed-code-generator-v1',
              'agentic-generation-v1'
            )
          ORDER BY rr.goal_id, p.created_at ASC
        )
        SELECT ref, count(*) FROM first_plan GROUP BY 1
    """), {"since": since}))
    report["goals_by_first_plan_arm"] = {r[0]: int(r[1]) for r in goal_rows}

    # Agentic run status + failure codes
    status_rows = list(c.execute(text("""
        SELECT r.status, count(*)
        FROM generation_plans p
        JOIN generation_runs r ON r.plan_id = p.id
        WHERE p.contract_json->>'generator_ref' = 'agentic-generation-v1'
          AND r.created_at >= :since
        GROUP BY 1 ORDER BY 2 DESC
    """), {"since": since}))
    report["agentic_run_status"] = {r[0]: int(r[1]) for r in status_rows}

    fail_rows = list(c.execute(text("""
        SELECT coalesce(r.failure_code, '(none)') AS code, count(*)
        FROM generation_plans p
        JOIN generation_runs r ON r.plan_id = p.id
        WHERE p.contract_json->>'generator_ref' = 'agentic-generation-v1'
          AND r.created_at >= :since
          AND r.status NOT IN ('completed', 'succeeded', 'success')
        GROUP BY 1 ORDER BY 2 DESC
        LIMIT 15
    """), {"since": since}))
    report["agentic_failure_codes_top"] = [
        {"code": r[0], "count": int(r[1])} for r in fail_rows
    ]

    # Artifact presence for agentic runs
    art = c.execute(text("""
        SELECT
          count(DISTINCT r.id) AS runs,
          count(DISTINCT f.generation_run_id) AS with_changeset
        FROM generation_plans p
        JOIN generation_runs r ON r.plan_id = p.id
        LEFT JOIN file_change_sets f ON f.generation_run_id = r.id
        WHERE p.contract_json->>'generator_ref' = 'agentic-generation-v1'
          AND r.created_at >= :since
    """), {"since": since}).one()
    report["agentic_artifacts"] = {
        "runs": int(art[0] or 0),
        "with_changeset": int(art[1] or 0),
    }

    # Guardrail coarse signals
    open_gaps = c.execute(text("""
        SELECT count(*) FROM human_tasks
        WHERE status = 'OPEN'
          AND task_type ILIKE '%DELIVERY_GAP%'
    """)).scalar()
    open_tasks = c.execute(text("""
        SELECT count(*) FROM human_tasks WHERE status = 'OPEN'
    """)).scalar()
    outbox = c.execute(text("""
        SELECT status, count(*) FROM outbox_events
        WHERE status IN ('FAILED', 'DEAD_LETTER', 'PENDING')
        GROUP BY 1
    """)).all()
    new_goals = c.execute(text("""
        SELECT count(*) FROM goals WHERE created_at >= :since
    """), {"since": since}).scalar()

    report["guardrails"] = {
        "open_delivery_gap_tasks": int(open_gaps or 0),
        "open_human_tasks": int(open_tasks or 0),
        "outbox": {r[0]: int(r[1]) for r in outbox},
        "new_goals_in_window": int(new_goals or 0),
        "kill_switch": bool(s.generation_strategy_kill_switch),
    }

    # Canary config sanity
    report["canary_armed"] = (
        s.generation_strategy == "artifact-backed"
        and bool(s.generation_strategy_canary_gate)
        and int(s.generation_strategy_canary_percent) == 5
        and not bool(s.generation_strategy_kill_switch)
    )

print(json.dumps(report, ensure_ascii=False, default=str))
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    remote = REMOTE.replace("__HOURS__", str(max(1, args.hours)))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/probe_m6_canary.py", "w") as f:
        f.write(remote)
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/probe_m6_canary.py regent-api:/tmp/probe_m6_canary.py && "
        "docker exec -w /app regent-api python /tmp/probe_m6_canary.py",
        timeout=120,
    )
    out = (o.read() + e.read()).decode("utf-8", "replace")
    ssh.close()

    # Extract last JSON object from stdout
    text = out.strip()
    start = text.find("{")
    if start < 0:
        print(out)
        raise SystemExit("probe produced no JSON")
    payload = json.loads(text[start:])
    payload["local_probed_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"WROTE {args.out}")


if __name__ == "__main__":
    main()
