#!/usr/bin/env python3
"""Start READY goals with idempotency_key."""
import paramiko
import time

def run_ssh(commands, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('118.31.171.159', port=22, username='root', password=password)

    for cmd in commands:
        print(f"\n{'='*60}")
        print(f"CMD: {cmd[:150]}")
        print('='*60)
        stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        if out:
            print(out)
        if err:
            print(f"STDERR: {err}")

    client.close()

password = '080900.UI'

commands = [
    # Start goal b653c27c (date/time display)
    """curl -s -w '\\nHTTP_STATUS:%{http_code}' -X POST http://localhost:8000/v1/goals/b653c27c-5e8c-464e-8fe0-16ab6f6ca89f/start -H 'Content-Type: application/json' -d '{"actor": "trial-user", "idempotency_key": "start-b653c27c-20260725"}'""",

    # Start goal 6a286e4e (customer support chatbot)
    """curl -s -w '\\nHTTP_STATUS:%{http_code}' -X POST http://localhost:8000/v1/goals/6a286e4e-b049-457e-81e4-5a105c94ed06/start -H 'Content-Type: application/json' -d '{"actor": "trial-user", "idempotency_key": "start-6a286e4e-20260725"}'""",
]

run_ssh(commands, password)

print("\n\n>>> Waiting 15 seconds...")
time.sleep(15)

psql = 'docker exec regent-postgres psql -U regent -d regent -t -c'

commands2 = [
    # Check goal status
    f"""{psql} "SELECT id, status, version, metadata->>'execution_stage' as exec_stage, metadata->>'execution_event_id' as exec_event FROM goals WHERE id IN ('b653c27c-5e8c-464e-8fe0-16ab6f6ca89f','6a286e4e-b049-457e-81e4-5a105c94ed06');" """,

    # Check for new outbox events for these goals
    f"""{psql} "SELECT id, event_type, aggregate_id, status, left(last_error, 100) as err FROM outbox_events WHERE aggregate_id IN ('b653c27c-5e8c-464e-8fe0-16ab6f6ca89f','6a286e4e-b049-457e-81e4-5a105c94ed06') ORDER BY occurred_at DESC LIMIT 10;" """,

    # Outbox overall status
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,

    # Worker logs
    "docker logs regent-worker --tail 15 2>&1",
]

run_ssh(commands2, password)
