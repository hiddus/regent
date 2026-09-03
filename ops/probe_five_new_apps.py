"""Status of the five newest apps created around 12:40–12:43 UTC."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]

PROJECTS = (
    "b0b20b5f-dbbb-4e33-b590-f38a15d2395b",
    "d7ca8e15-74d4-4666-881b-adce5a9846e5",
    "29f07884-2dca-46d8-975c-3359aa803d89",
    "60095bbc-55aa-4d49-983c-f7ad4dc5b4b8",
    "99570348-3cf8-4565-a16c-4940f8a8d07d",
)


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

    def run(cmd: str, timeout: int = 120) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=timeout)
        return (o.read() + e.read()).decode("utf-8", "replace")

    ids = ",".join(f"'{p}'" for p in PROJECTS)
    print("=== five newest projects + goals ===")
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT p.id::text AS project_id, p.status AS p_status, convert_from(convert_to(p.name,'UTF8'),'UTF8') AS name,
       g.id::text AS goal_id, g.status AS g_status,
       g.created_at, g.updated_at,
       extract(epoch from (now()-g.updated_at))::int AS idle_sec
FROM app_projects p
LEFT JOIN goals g ON g.app_project_id = p.id
WHERE p.id IN ({ids})
ORDER BY p.created_at DESC, g.created_at DESC;"
"""
        )
    )

    print("=== generation_runs for these goals ===")
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT column_name FROM information_schema.columns
WHERE table_name='generation_runs' ORDER BY ordinal_position;"
"""
        )
    )
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT gr.id::text, gr.status, gr.failure_code, gr.created_at, gr.updated_at,
       g.id::text AS goal_id, g.status AS goal_status
FROM generation_runs gr
JOIN goals g ON g.id = gr.goal_id
WHERE g.app_project_id IN ({ids})
ORDER BY gr.created_at DESC
LIMIT 30;"
"""
        )
    )

    print("=== app_builds columns + rows ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT column_name FROM information_schema.columns
WHERE table_name='app_builds' ORDER BY ordinal_position;"
"""
        )
    )
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT * FROM app_builds
WHERE project_id::text IN ({ids}) OR app_project_id::text IN ({ids})
ORDER BY created_at DESC LIMIT 20;"
"""
        )
    )

    print("=== conversation / live action hints ===")
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND (
  table_name ILIKE '%message%' OR table_name ILIKE '%chat%' OR table_name ILIKE '%live%'
) ORDER BY 1;"
"""
        )
    )

    print("=== pending generation outbox for these ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, event_type, status, attempts, available_at, updated_at,
       left(payload::text, 120) AS payload_head
FROM outbox_events
WHERE event_type='GenerationRunRequested'
  AND status IN ('PENDING','FAILED','DEAD_LETTER','DISPATCHING')
ORDER BY updated_at DESC
LIMIT 15;"
"""
        )
    )

    print("=== worker logs tail ===")
    print(
        run(
            "docker logs --tail 30 regent-worker 2>&1; echo '---'; "
            "docker logs --tail 15 regent-worker-2 2>&1"
        )
    )
    ssh.close()


if __name__ == "__main__":
    main()
