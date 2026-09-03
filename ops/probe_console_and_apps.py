"""Diagnose console staleness + recent apps + stuck delivery on S0."""
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

    def run(cmd: str, timeout: int = 120) -> tuple[str, int]:
        _, o, e = ssh.exec_command(cmd, timeout=timeout)
        out = (o.read() + e.read()).decode("utf-8", "replace")
        return out, o.channel.recv_exit_status()

    print("=== console asset probe ===")
    out, _ = run(
        "docker exec regent-api sh -lc "
        "'ls -la /app/apps/regent-console/dist/assets 2>/dev/null | head -10; "
        "ls -la /opt/regent/console 2>/dev/null | head -10; "
        "find /app -name index.html 2>/dev/null | head -15'"
    )
    print(out)
    out, _ = run(
        "curl -sI --max-time 5 http://127.0.0.1:8000/console/ | head -20; "
        "echo '--- body head ---'; "
        "curl -s --max-time 5 http://127.0.0.1:8000/console/ | head -c 500; echo"
    )
    print(out)
    out, _ = run(
        "docker exec regent-api sh -lc "
        "'ls /app/apps/regent-console/dist/assets 2>/dev/null; "
        "stat -c \"%y %n\" /app/apps/regent-console/dist/index.html 2>/dev/null; "
        "stat -c \"%y %n\" /opt/regent/console/index.html 2>/dev/null'"
    )
    print("mtime:\n", out)

    print("=== recent goals ===")
    out, _ = run(
        """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT id::text, left(coalesce(title, goal_text, ''), 48) AS title, status,
       created_at, updated_at
FROM goals
ORDER BY created_at DESC
LIMIT 10;"
"""
    )
    print(out)

    print("=== recent app projects if table exists ===")
    out, _ = run(
        """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name ILIKE '%app%'
ORDER BY 1;"
"""
    )
    print(out)

    print("=== stuck signals ===")
    out, _ = run(
        """docker exec -i regent-postgres psql -U regent -d regent -c "
SELECT status, count(*) FROM outbox_messages GROUP BY 1 ORDER BY 1;
SELECT status, count(*) FROM goals WHERE status NOT IN ('ACHIEVED','CANCELLED','FAILED','EXHAUSTED') GROUP BY 1 ORDER BY 1;
SELECT status, count(*) FROM generation_runs WHERE created_at > now() - interval '6 hours' GROUP BY 1 ORDER BY 1;
"
"""
    )
    print(out)

    print("=== health ===")
    out, _ = run("curl -s --max-time 8 http://127.0.0.1:8000/health/ready; echo")
    print(out)
    ssh.close()


if __name__ == "__main__":
    main()
