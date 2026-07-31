"""Build GQ-3 dual-arm report from production Goals on S0 (or local DB).

Usage:
  python ops/gq3_production_report.py
  python ops/gq3_production_report.py --since 2026-07-31T10:00:00+00:00 --max-days 21

Writes:
  docs/gq3-experiment-report-<utc-date>.json
  and prints decision + apply_gq4 gate preview (does NOT flip env).
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

REMOTE_LOADER = r'''
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from regent.config import get_settings
from regent.application.gq3_production_report import (
    GoalArmObservation,
    build_production_experiment,
    enrich_report,
    variant_from_generator_ref,
)
from regent.application.generation_strategy_promotion import evaluate_gq4_promotion

SINCE = __SINCE_REPR__
UNTIL = __UNTIL_REPR__
OPENED = __OPENED_REPR__
MAX_DAYS = __MAX_DAYS__

url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)

# Intent-to-treat: first generation plan per goal in the window → arm.
# Joins: goals → requirement_revisions → generation_plans → generation_runs
# Human tasks + metadata for repair / intervention.
# Build time filter in Python to avoid NULL param type ambiguity in PG.
until_clause = "" if UNTIL is None else "AND p.created_at < :until"
until_clause2 = "" if UNTIL is None else "AND p2.created_at < :until"
params = {"since": SINCE}
if UNTIL is not None:
    params["until"] = UNTIL

sql = text(f"""
WITH first_plan AS (
  SELECT DISTINCT ON (rr.goal_id)
    rr.goal_id,
    p.id AS plan_id,
    p.created_at AS plan_at,
    p.contract_json->>'generator_ref' AS generator_ref
  FROM generation_plans p
  JOIN requirement_revisions rr ON rr.id = p.requirement_revision_id
  WHERE p.created_at >= :since
    {until_clause}
    AND p.contract_json->>'generator_ref' IN (
      'artifact-backed-code-generator-v1',
      'agentic-generation-v1'
    )
  ORDER BY rr.goal_id, p.created_at ASC
),
run_agg AS (
  SELECT
    fp.goal_id,
    COALESCE(SUM(r.input_tokens), 0) AS input_tokens,
    COALESCE(SUM(r.output_tokens), 0) AS output_tokens,
    COALESCE(
      EXTRACT(EPOCH FROM (MAX(r.updated_at) - MIN(r.created_at))) * 1000,
      0
    ) AS latency_ms,
    BOOL_OR(
      COALESCE(r.failure_code, '') ILIKE '%SAFETY%'
      OR COALESCE(r.failure_code, '') ILIKE '%POLICY%'
    ) AS safety_incident
  FROM first_plan fp
  JOIN generation_plans p ON p.id IN (
    SELECT p2.id FROM generation_plans p2
    JOIN requirement_revisions rr2 ON rr2.id = p2.requirement_revision_id
    WHERE rr2.goal_id = fp.goal_id
      AND p2.created_at >= :since
      {until_clause2}
  )
  LEFT JOIN generation_runs r ON r.plan_id = p.id
  GROUP BY fp.goal_id
),
human AS (
  SELECT goal_id, COUNT(*) > 0 AS intervened
  FROM human_tasks
  GROUP BY goal_id
)
SELECT
  g.id::text AS goal_id,
  g.status AS goal_status,
  fp.generator_ref,
  fp.plan_at,
  COALESCE((g."metadata"->>'delivery_gap_recovery_attempts')::int, 0) AS repair_rounds,
  COALESCE(h.intervened, false) AS human_intervened,
  COALESCE(ra.input_tokens, 0) AS input_tokens,
  COALESCE(ra.output_tokens, 0) AS output_tokens,
  COALESCE(ra.latency_ms, 0) AS latency_ms,
  COALESCE(ra.safety_incident, false) AS safety_incident
FROM first_plan fp
JOIN goals g ON g.id = fp.goal_id
LEFT JOIN run_agg ra ON ra.goal_id = fp.goal_id
LEFT JOIN human h ON h.goal_id = fp.goal_id
ORDER BY fp.plan_at ASC
""")

observations = []
with eng.connect() as c:
    rows = c.execute(sql, params).mappings().all()
    for row in rows:
        variant = variant_from_generator_ref(row["generator_ref"])
        if variant is None:
            continue
        plan_at = row["plan_at"]
        observations.append(GoalArmObservation(
            goal_id=str(row["goal_id"]),
            variant=variant,
            goal_status=str(row["goal_status"]),
            repair_rounds=int(row["repair_rounds"] or 0),
            human_intervened=bool(row["human_intervened"]),
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            latency_ms=float(row["latency_ms"] or 0),
            first_plan_at=plan_at.isoformat() if plan_at is not None else None,
            generator_ref=str(row["generator_ref"]),
            safety_incident=bool(row["safety_incident"]),
        ))

exp = build_production_experiment(observations)
report = exp.report(actor="gq3-production-report")
report = enrich_report(
    report,
    observations=observations,
    window_opened_at=OPENED,
    window_max_days=MAX_DAYS,
    since=SINCE,
    until=UNTIL,
)
gate = evaluate_gq4_promotion(
    report,
    kill_switch=False,
    decision_record_ref="pending-gq4-decision-note",
)
out = {
    "report": report,
    "gq4_gate_preview": gate,
    "observations_n": len(observations),
}
print(json.dumps(out, ensure_ascii=False, default=str))
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="GQ-3 production dual-arm report")
    parser.add_argument(
        "--since",
        default="2026-07-31T10:00:00+00:00",
        help="ISO timestamp: canary window open (UTC)",
    )
    parser.add_argument("--until", default=None, help="ISO upper bound (exclusive)")
    parser.add_argument(
        "--opened-at",
        default=None,
        help="Window opened_at for expiry (default: --since)",
    )
    parser.add_argument("--max-days", type=int, default=21, help="Max window days")
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default docs/gq3-experiment-report-DATE.json)",
    )
    args = parser.parse_args()
    opened = args.opened_at or args.since

    remote = (
        REMOTE_LOADER.replace("__SINCE_REPR__", repr(args.since))
        .replace("__UNTIL_REPR__", repr(args.until))
        .replace("__OPENED_REPR__", repr(opened))
        .replace("__MAX_DAYS__", str(int(args.max_days)))
    )

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    # Ensure latest helper module is on the API container.
    sftp = ssh.open_sftp()
    local_mod = ROOT / "core/src/regent/application/gq3_production_report.py"
    with sftp.file("/tmp/gq3_production_report.py", "wb") as f:
        f.write(local_mod.read_bytes())
    with sftp.file("/tmp/gq3_run_report.py", "wb") as f:
        f.write(remote.encode("utf-8"))
    sftp.close()

    # Sanity: placeholders must be gone before docker cp.
    _, o_chk, _ = ssh.exec_command("head -20 /tmp/gq3_run_report.py", timeout=30)
    head = o_chk.read().decode("utf-8", "replace")
    if "__SINCE_REPR__" in head:
        print("BAD_UPLOAD:\n", head)
        raise SystemExit("remote script still has placeholders")
    print("upload_head_ok")

    cmd = r"""
set -e
SITE=/usr/local/lib/python3.12/site-packages/regent
docker cp /tmp/gq3_production_report.py regent-api:$SITE/application/gq3_production_report.py
docker cp /tmp/gq3_run_report.py regent-api:/tmp/gq3_run_report.py
docker exec -w /app regent-api python /tmp/gq3_run_report.py
"""
    _, o, e = ssh.exec_command(cmd, timeout=120)
    raw = (o.read() + e.read()).decode("utf-8", "replace")
    ssh.close()

    # Last JSON object in stdout
    lines = [ln for ln in raw.splitlines() if ln.strip().startswith("{")]
    if not lines:
        print(raw)
        raise SystemExit("no JSON report from server")
    payload = json.loads(lines[-1])
    report = payload["report"]
    gate = payload["gq4_gate_preview"]

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out_path = Path(args.out) if args.out else ROOT / "docs" / f"gq3-experiment-report-{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {out_path}")
    print(f"n_goals={payload.get('observations_n')}")
    print(f"decision={report.get('decision')} rationale={report.get('rationale')}")
    print(f"summaries={json.dumps(report.get('summaries'), ensure_ascii=False)}")
    print(f"stop_rule={report.get('stop_rule')}")
    print(
        f"gq4_gate activation_allowed={gate.get('activation_allowed')} "
        f"reason={gate.get('reason')}"
    )
    if report.get("decision") != "PROMOTE_AGENTIC_CANDIDATE":
        print("GQ4_ACTION=keep PENDING (do not flip REGENT_GENERATION_STRATEGY)")
    else:
        print(
            "GQ4_ACTION=eligible — write ACCEPTED DecisionRecord then "
            "apply_gq4_promotion + flip env"
        )


if __name__ == "__main__":
    main()
