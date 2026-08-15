"""Idempotently add missing confirmation gates for confirmable draft goals."""
from __future__ import annotations

import argparse
from pathlib import Path
import uuid

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]

SQL_TEMPLATE = r"""
BEGIN;
CREATE TEMP TABLE confirmable ON COMMIT DROP AS
SELECT g.id AS goal_id,
       g.app_project_id AS project_id,
       s.id AS spec_id,
       s.version AS spec_version,
       s.content_hash AS spec_hash,
       s.unknowns,
       s.success_criteria,
       s.explicit_constraints,
       s.system_inferences,
       g.metadata,
       'goal:' || g.id::text || ':spec:' || s.version::text || ':confirm' AS gate_key
FROM goals g
JOIN LATERAL (
  SELECT * FROM goal_specs WHERE goal_id=g.id ORDER BY version DESC LIMIT 1
) s ON true
WHERE g.status='DRAFT'
  AND g.app_project_id='__PROJECT_ID__'::uuid
  AND coalesce((g.metadata->>'clarification_rounds')::int, 0) >= 2
  AND upper(coalesce(g.metadata->>'feasibility_verdict','')) IN ('FEASIBLE','REVISION_REQUIRED')
  AND coalesce((g.metadata->>'execution_boundary_locked')::boolean, false)=false
  AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(coalesce(s.unknowns, '[]'::jsonb)) item
    WHERE coalesce((item->>'blocking')::boolean, true)
  );

SELECT 1 FROM conversations c
JOIN confirmable x ON x.project_id=c.app_project_id
FOR UPDATE;

UPDATE goals g
SET metadata = g.metadata || jsonb_build_object(
  'feasibility_verdict', 'FEASIBLE',
  'goal_spec_hash', x.spec_hash,
  'latest_goal_spec_version', x.spec_version,
  'goal_phase', 'DRAFT_CONFIRMABLE',
  'goal_clarity_state', 'WAITING_CONFIRMATION',
  'confirmation_state', 'PENDING',
  'confirmation_gate_key', x.gate_key,
  'unknowns', x.unknowns
)
FROM confirmable x WHERE g.id=x.goal_id;

WITH missing AS (
  SELECT x.*, c.id AS conversation_id,
         coalesce((SELECT max(m.ordinal) FROM conversation_messages m WHERE m.conversation_id=c.id), 0) + 1 AS ordinal
  FROM confirmable x
  JOIN conversations c ON c.app_project_id=x.project_id
  WHERE NOT EXISTS (
    SELECT 1 FROM conversation_messages m
    WHERE m.conversation_id=c.id
      AND m.message_type='APP_CONFIRMATION_REQUIRED'
      AND m.metadata_json->>'gate_key'=x.gate_key
  )
)
INSERT INTO conversation_messages (
  id, conversation_id, ordinal, role, message_type, content,
  metadata_json, created_by, created_at
)
SELECT gen_random_uuid(), conversation_id, ordinal, 'ASSISTANT',
       'APP_CONFIRMATION_REQUIRED',
       '可行性分析已通过，目标已恢复为可确认状态。请确认并锁定当前目标后再开始执行；确认前不会开工。',
       jsonb_build_object(
         'app_project_id', project_id,
         'goal_id', goal_id,
         'goal_spec_id', spec_id,
         'goal_spec_version', spec_version,
         'goal_spec_hash', spec_hash,
         'goal_spec_status', 'DRAFT',
         'feasibility_verdict', 'FEASIBLE',
         'feasibility_reasons', coalesce(metadata->'feasibility_reasons', '[]'::jsonb),
         'clarification_rounds', coalesce((metadata->>'clarification_rounds')::int, 0),
         'unknowns', unknowns,
         'blocking_unknowns', '[]'::jsonb,
         'understanding', system_inferences || jsonb_build_object(
           'explicit_constraints', explicit_constraints,
           'success_criteria', success_criteria,
           'unknowns', unknowns
         ),
         'plan', coalesce(metadata->'runtime_plan', '{}'::jsonb),
         'gate_key', gate_key,
         'gate_status', 'PENDING',
         'next_action', 'CONFIRM_GOAL',
         'reconciled', true
       ),
       'regent-core:confirmation-reconciler', now()
FROM missing
RETURNING conversation_id, ordinal, metadata_json->>'gate_key' AS gate_key;
COMMIT;
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    project_id = str(uuid.UUID(args.project_id))
    sql = SQL_TEMPLATE.replace("__PROJECT_ID__", project_id)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    stdin, stdout, stderr = ssh.exec_command(
        "docker exec -i regent-postgres psql -X -v ON_ERROR_STOP=1 -U regent -d regent -P pager=off",
        timeout=120,
    )
    stdin.write(sql)
    stdin.channel.shutdown_write()
    output = (stdout.read() + stderr.read()).decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    ssh.close()
    print(output)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
