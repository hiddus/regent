"""Promote the known draft only when two rounds are done and no blockers remain."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]
PROJECT_ID = "8aed3662-adca-4017-9d3e-baebd22d02ba"


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    sql = f"""
WITH candidate AS (
  SELECT g.id, s.content_hash
  FROM goals g
  JOIN LATERAL (
    SELECT unknowns, content_hash FROM goal_specs
    WHERE goal_id=g.id ORDER BY version DESC LIMIT 1
  ) s ON true
  WHERE g.app_project_id='{PROJECT_ID}'
    AND g.status='DRAFT'
    AND coalesce((g.metadata->>'clarification_rounds')::int, 0) >= 2
    AND upper(coalesce(g.metadata->>'feasibility_verdict','')) IN ('REVISION_REQUIRED','FEASIBLE')
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(coalesce(s.unknowns, '[]'::jsonb)) item
      WHERE coalesce((item->>'blocking')::boolean, true)
    )
  ORDER BY g.created_at DESC LIMIT 1
)
UPDATE goals g
SET metadata=jsonb_set(g.metadata, '{{feasibility_verdict}}', to_jsonb('FEASIBLE'::text), true)
             || jsonb_build_object('goal_spec_hash', c.content_hash)
FROM candidate c WHERE g.id=c.id
RETURNING g.id, g.status, g.metadata->>'clarification_rounds' AS rounds,
          g.metadata->>'feasibility_verdict' AS verdict;
"""
    command = (
        "docker exec -i regent-postgres psql -X -U regent -d regent "
        f"-P pager=off -c \"{sql}\""
    )
    _, stdout, stderr = ssh.exec_command(command, timeout=60)
    output = (stdout.read() + stderr.read()).decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    ssh.close()
    print(output)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
