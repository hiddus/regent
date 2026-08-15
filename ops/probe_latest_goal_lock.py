"""Read-only diagnosis for the newest app project's clarification/lock state."""
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

    def query(sql: str) -> str:
        command = (
            "docker exec -i regent-postgres psql -X -U regent -d regent "
            f"-P pager=off -c \"{sql}\""
        )
        _, stdout, stderr = ssh.exec_command(command, timeout=60)
        return (stdout.read() + stderr.read()).decode("utf-8", "replace")

    print("=== goal and latest spec ===")
    print(query(f"""
SELECT g.id, g.status, g.version,
       g.metadata->>'clarification_rounds' AS rounds,
       g.metadata->>'feasibility_verdict' AS verdict,
       g.metadata->>'execution_boundary_locked' AS locked,
       g.metadata->>'locked_spec_hash' AS locked_hash,
       g.metadata->>'goal_spec_hash' AS current_hash,
       s.version AS spec_version, s.status AS spec_status,
       s.content_hash, s.confirmed_by,
       jsonb_pretty(s.unknowns) AS unknowns,
       jsonb_pretty(s.explicit_constraints) AS constraints
FROM goals g
JOIN LATERAL (
  SELECT * FROM goal_specs WHERE goal_id=g.id ORDER BY version DESC LIMIT 1
) s ON true
WHERE g.app_project_id='{PROJECT_ID}'
ORDER BY g.created_at DESC LIMIT 1;
"""))

    print("=== recent conversation ===")
    print(query(f"""
SELECT m.ordinal, m.role, m.message_type,
       left(replace(m.content, chr(10), ' '), 260) AS content,
       m.metadata_json->>'command_type' AS command_type,
       m.created_at
FROM conversation_messages m
JOIN conversations c ON c.id=m.conversation_id
WHERE c.app_project_id='{PROJECT_ID}'
ORDER BY m.ordinal DESC LIMIT 24;
"""))
    ssh.close()


if __name__ == "__main__":
    main()
