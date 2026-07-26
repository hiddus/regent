#!/usr/bin/env python3
"""Try to start READY goals via API."""
import paramiko
import json
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

# Try to start goal b653c27c (date/time display) via API
commands = [
    # Try starting goal b653c27c
    """curl -s -w '\\nHTTP_STATUS:%{http_code}' -X POST http://localhost:8000/v1/goals/b653c27c-5e8c-464e-8fe0-16ab6f6ca89f/start -H 'Content-Type: application/json' -d '{"actor": "trial-user"}'""",

    # Try starting goal 6a286e4e (customer support chatbot)
    """curl -s -w '\\nHTTP_STATUS:%{http_code}' -X POST http://localhost:8000/v1/goals/6a286e4e-b049-457e-81e4-5a105c94ed06/start -H 'Content-Type: application/json' -d '{"actor": "trial-user"}'""",
]

run_ssh(commands, password)

print("\n\n>>> Waiting 10 seconds for events to be created...")
time.sleep(10)

psql = 'docker exec regent-postgres psql -U regent -d regent -t -c'

# Check results
commands2 = [
    # Check goal status
    f"""{psql} "SELECT id, status, version, metadata->>'execution_stage' as exec_stage FROM goals WHERE id IN ('b653c27c-5e8c-464e-8fe0-16ab6f6ca89f','6a286e4e-b049-457e-81e4-5a105c94ed06');" """,

    # Check for new outbox events
    f"""{psql} "SELECT id, event_type, aggregate_id, status FROM outbox_events WHERE aggregate_id IN ('b653c27c-5e8c-464e-8fe0-16ab6f6ca89f','6a286e4e-b049-457e-81e4-5a105c94ed06') ORDER BY occurred_at DESC LIMIT 10;" """,

    # Check outbox overall status
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,

    # Worker logs
    "docker logs regent-worker --tail 10 2>&1",
]

run_ssh(commands2, password)
