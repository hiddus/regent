"""Detailed status of newest apps/goals after console redeploy."""
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

    print("=== latest 10 goals ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT g.id::text,
       g.status,
       left(coalesce(g.original_input,''), 48) AS input,
       g.app_project_id::text,
       g.created_at,
       g.updated_at,
       coalesce(g.metadata->>'halt_stage', g.metadata->>'stage', '') AS stage
FROM goals g
ORDER BY g.created_at DESC
LIMIT 10;"
"""
        )
    )

    print("=== latest 8 app_projects ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, status, left(coalesce(name,''), 40) AS name,
       left(coalesce(product_intent,''), 40) AS intent,
       created_at, updated_at
FROM app_projects
ORDER BY created_at DESC
LIMIT 8;"
"""
        )
    )

    print("=== app builds / previews for newest projects ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
WITH recent AS (
  SELECT id FROM app_projects ORDER BY created_at DESC LIMIT 5
)
SELECT p.id::text AS project_id,
       left(p.name, 28) AS name,
       p.status AS project_status,
       b.id::text AS build_id,
       b.status AS build_status,
       r.id::text AS release_id,
       r.status AS release_status,
       left(coalesce(r.preview_url, r.endpoint, ''), 50) AS preview
FROM app_projects p
LEFT JOIN LATERAL (
  SELECT * FROM app_builds b WHERE b.app_project_id = p.id
  ORDER BY b.created_at DESC LIMIT 1
) b ON true
LEFT JOIN LATERAL (
  SELECT * FROM app_preview_releases r WHERE r.app_project_id = p.id
  ORDER BY r.created_at DESC LIMIT 1
) r ON true
WHERE p.id IN (SELECT id FROM recent)
ORDER BY p.created_at DESC;"
"""
        )
    )

    print("=== outbox_events by status ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT status, count(*) FROM outbox_events GROUP BY 1 ORDER BY 1;
SELECT event_type, status, count(*)
FROM outbox_events
WHERE status IN ('PENDING','FAILED','DEAD_LETTER','CLAIMED','RUNNING')
GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20;"
"""
        )
    )

    print("=== generation_runs last 6h ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT status, count(*) FROM generation_runs
WHERE created_at > now() - interval '6 hours'
GROUP BY 1 ORDER BY 1;
SELECT gr.id::text, gr.status, gr.failure_code,
       left(coalesce(gr.failure_detail, gr.error_message, ''), 60) AS detail,
       gr.created_at, gr.updated_at
FROM generation_runs gr
ORDER BY gr.created_at DESC LIMIT 10;"
"""
        )
    )

    print("=== waiting human tasks open ===")
    print(
        run(
            """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT status, count(*) FROM human_tasks GROUP BY 1 ORDER BY 1;
SELECT id::text, status, task_type, left(coalesce(prompt,''), 50), created_at
FROM human_tasks
WHERE status IN ('OPEN','PENDING','WAITING')
ORDER BY created_at DESC LIMIT 10;"
"""
        )
    )

    print("=== health ===")
    print(run("curl -s --max-time 8 http://127.0.0.1:8000/health/ready; echo"))
    print("=== console asset now ===")
    print(
        run(
            "curl -s --max-time 5 http://127.0.0.1:8000/console/ | "
            "grep -oE 'assets/index-[A-Za-z0-9_-]+\\.js' | head -3"
        )
    )
    ssh.close()


if __name__ == "__main__":
    main()
