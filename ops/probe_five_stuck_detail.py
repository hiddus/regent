"""Deep stuck diagnosis for five newest ACTIVE goals."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]

GOALS = (
    "3a43e2f3-a092-4500-a2ee-1e61c89f9248",
    "8b9ac7bd-6238-493d-aac0-200acf3ec635",
    "c3e5b264-0013-4b1f-a7f7-f07793862887",
    "5564fba7-b285-404a-9c1e-eee277b241ac",
    "971c0b3d-19bf-4059-8d44-028a50691629",
)


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

    def run(cmd: str, timeout: int = 120) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=timeout)
        return (o.read() + e.read()).decode("utf-8", "replace")

    gids = ",".join(f"'{g}'" for g in GOALS)

    print("=== conversation_messages columns ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT column_name FROM information_schema.columns
WHERE table_name='conversation_messages' ORDER BY ordinal_position;"
"""
        )
    )
    print("=== recent messages for 5 goals ===")
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT goal_id::text, message_type, left(coalesce(content, body, text, ''), 80) AS body,
       created_at
FROM conversation_messages
WHERE goal_id::text IN ({gids})
ORDER BY created_at DESC
LIMIT 40;"
"""
        )
    )

    print("=== generation_plans columns + rows ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT column_name FROM information_schema.columns
WHERE table_name='generation_plans' ORDER BY ordinal_position;"
"""
        )
    )
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, status, goal_id::text, created_at, updated_at
FROM generation_plans
WHERE goal_id::text IN ({gids})
ORDER BY created_at DESC;"
"""
        )
    )

    print("=== generation_runs via plans ===")
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT gr.id::text, gr.status, gr.failure_code, gr.attempt, gr.created_at, gr.updated_at,
       gp.goal_id::text
FROM generation_runs gr
JOIN generation_plans gp ON gp.id = gr.plan_id
WHERE gp.goal_id::text IN ({gids})
ORDER BY gr.created_at DESC
LIMIT 40;"
"""
        )
    )

    print("=== outbox pending gen ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, event_type, status, attempt, available_at, updated_at,
       left(payload::text, 160) AS payload_head
FROM outbox_events
WHERE event_type IN ('GenerationRunRequested','DeliveryStateChanged','RequirementRequested')
  AND status IN ('PENDING','FAILED','DEAD_LETTER','DISPATCHING')
ORDER BY updated_at DESC
LIMIT 20;"
"""
        )
    )

    print("=== goal metadata snapshot ===")
    print(
        run(
            f"""docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, status, left(metadata::text, 240) AS meta
FROM goals WHERE id::text IN ({gids});"
"""
        )
    )
    ssh.close()


if __name__ == "__main__":
    main()
