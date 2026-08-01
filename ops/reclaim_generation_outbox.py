"""Reclaim poisoned / stuck generation-related outbox events.

Default is dry-run (print counts + sample rows). Pass --execute to apply.

Targets:
  - DeliveryStateChanged FAILED/DEAD_LETTER → DISPATCHED (ack poison; no handler side effects)
  - GenerationRunRequested DISPATCHING with expired lease → PENDING
  - GenerationRunRequested FAILED (retryable errors) → PENDING
  - With --include-path-errors: also reclaim FAILED containing "outside frozen plan"
    or "cannot mark FAILED_TERMINAL" after code fixes landed.

Usage:
  python ops/reclaim_generation_outbox.py
  python ops/reclaim_generation_outbox.py --execute
  python ops/reclaim_generation_outbox.py --remote --execute --include-path-errors
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

REMOTE_SQL_PROBE = r"""
SELECT event_type, status, COUNT(*) AS n
FROM outbox_events
WHERE event_type IN ('DeliveryStateChanged', 'GenerationRunRequested')
GROUP BY 1, 2
ORDER BY 1, 2;
"""

REMOTE_SQL_EXECUTE = r"""
BEGIN;

-- Ack DeliveryStateChanged poison (handler is now registered; old failures need drain).
UPDATE outbox_events
SET status = 'DISPATCHED',
    dispatched_at = COALESCE(dispatched_at, NOW()),
    lease_owner = NULL,
    lease_expires_at = NULL,
    last_error = NULL
WHERE event_type = 'DeliveryStateChanged'
  AND status IN ('FAILED', 'DEAD_LETTER', 'PENDING', 'DISPATCHING');

-- Reclaim expired GenerationRunRequested leases.
UPDATE outbox_events
SET status = 'PENDING',
    lease_owner = NULL,
    lease_expires_at = NULL,
    available_at = NOW(),
    last_error = NULL
WHERE event_type = 'GenerationRunRequested'
  AND status = 'DISPATCHING'
  AND (lease_expires_at IS NULL OR lease_expires_at < NOW());

-- Retryable FAILED generation events (and optional non-retryable after code fix).
UPDATE outbox_events
SET status = 'PENDING',
    lease_owner = NULL,
    lease_expires_at = NULL,
    available_at = NOW(),
    last_error = NULL
WHERE event_type = 'GenerationRunRequested'
  AND status IN ('FAILED', 'DEAD_LETTER')
  AND (
    last_error IS NULL
    OR last_error NOT LIKE '[non-retryable]%'
    OR __INCLUDE_PATH_ERRORS__
  );

COMMIT;

SELECT event_type, status, COUNT(*) AS n
FROM outbox_events
WHERE event_type IN ('DeliveryStateChanged', 'GenerationRunRequested')
GROUP BY 1, 2
ORDER BY 1, 2;
"""


def _run_ssh(cmd: str, *, timeout: int = 120) -> tuple[str, int]:
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing in .env")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    try:
        _, o, e = ssh.exec_command(cmd, timeout=timeout)
        out = (o.read() + e.read()).decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        return out, code
    finally:
        ssh.close()


def _psql(sql: str) -> str:
    # Escape for heredoc
    return f"""docker exec -i regent-postgres psql -U regent -d regent -v ON_ERROR_STOP=1 <<'SQL'
{sql}
SQL"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Apply updates (default dry-run)")
    parser.add_argument(
        "--remote",
        action="store_true",
        default=True,
        help="Run against S0 via SSH (default)",
    )
    parser.add_argument("--local", action="store_true", help="Print SQL only (no SSH)")
    parser.add_argument(
        "--include-path-errors",
        action="store_true",
        help="Also reclaim non-retryable path/FAILED_TERMINAL generation failures",
    )
    args = parser.parse_args()
    if args.local:
        args.remote = False

    include = "TRUE" if args.include_path_errors else "FALSE"
    execute_sql = REMOTE_SQL_EXECUTE.replace("__INCLUDE_PATH_ERRORS__", include)

    if not args.remote:
        print("=== DRY SQL (not executed) ===")
        print(REMOTE_SQL_PROBE if not args.execute else execute_sql)
        return 0

    print(f"S0 {HOST} probe…")
    out, code = _run_ssh(_psql(REMOTE_SQL_PROBE))
    print(out)
    if code != 0:
        return code

    if not args.execute:
        print("Dry-run only. Re-run with --execute to apply.")
        return 0

    print("Executing reclaim…")
    out, code = _run_ssh(_psql(execute_sql))
    print(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
