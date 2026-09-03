"""Check recent 5 apps/goals and stuck delivery after recreate."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

    def run(cmd: str, timeout: int = 120) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=timeout)
        return (o.read() + e.read()).decode("utf-8", "replace")

    print("=== goals columns sample ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT column_name FROM information_schema.columns
WHERE table_name='goals' ORDER BY ordinal_position;"
"""
        )
    )

    print("=== latest 8 goals ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, status, left(coalesce(goal_text,''), 56) AS goal_text,
       created_at, updated_at
FROM goals ORDER BY created_at DESC LIMIT 8;"
"""
        )
    )

    print("=== latest app_projects ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT column_name FROM information_schema.columns
WHERE table_name='app_projects' ORDER BY ordinal_position LIMIT 40;"
"""
        )
    )
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, status, left(coalesce(name, title, ''), 40) AS name,
       created_at, updated_at
FROM app_projects ORDER BY created_at DESC LIMIT 8;"
"""
        )
    )

    print("=== outbox / gen runs ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND (table_name ILIKE '%outbox%' OR table_name ILIKE '%generation%')
ORDER BY 1;"
"""
        )
    )
    ssh.close()


if __name__ == "__main__":
    main()
