"""Read-only production invariants for draft confirmation gates."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]

SQL = r"""
WITH latest AS (
  SELECT g.id AS goal_id, g.app_project_id, g.status, g.metadata,
         s.version, s.content_hash, s.unknowns,
         'goal:' || g.id::text || ':spec:' || s.version::text || ':confirm' AS gate_key
  FROM goals g
  JOIN LATERAL (
    SELECT * FROM goal_specs WHERE goal_id=g.id ORDER BY version DESC LIMIT 1
  ) s ON true
), confirmable AS (
  SELECT * FROM latest l
  WHERE status='DRAFT'
    AND coalesce((metadata->>'clarification_rounds')::int,0) >= 2
    AND upper(coalesce(metadata->>'feasibility_verdict',''))='FEASIBLE'
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(coalesce(unknowns,'[]'::jsonb)) item
      WHERE coalesce((item->>'blocking')::boolean,true)
    )
), gates AS (
  SELECT c.app_project_id, m.metadata_json->>'gate_key' AS gate_key,
         m.metadata_json->>'goal_spec_hash' AS gate_hash, count(*) AS n
  FROM conversations c
  JOIN conversation_messages m ON m.conversation_id=c.id
  WHERE m.message_type='APP_CONFIRMATION_REQUIRED'
    AND coalesce(m.metadata_json->>'gate_status','PENDING')='PENDING'
  GROUP BY c.app_project_id, m.metadata_json->>'gate_key', m.metadata_json->>'goal_spec_hash'
)
SELECT
  (SELECT count(*) FROM confirmable c
   WHERE NOT EXISTS (SELECT 1 FROM gates g WHERE g.app_project_id=c.app_project_id AND g.gate_key=c.gate_key))
    AS draft_confirmable_without_pending_gate,
  (SELECT count(*) FROM confirmable c JOIN gates g ON g.app_project_id=c.app_project_id
   WHERE g.gate_key=c.gate_key AND g.gate_hash<>c.content_hash)
    AS pending_gate_hash_mismatch,
  (SELECT count(*) FROM gates WHERE n>1)
    AS duplicate_pending_gate_keys;
"""


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    stdin, stdout, stderr = ssh.exec_command(
        "docker exec -i regent-postgres psql -X -v ON_ERROR_STOP=1 -U regent -d regent -P pager=off",
        timeout=60,
    )
    stdin.write(SQL)
    stdin.channel.shutdown_write()
    output = (stdout.read() + stderr.read()).decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    ssh.close()
    print(output)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
