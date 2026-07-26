#!/usr/bin/env python3
"""Comprehensive status check with correct DB credentials."""
import paramiko

def run_ssh(commands, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('118.31.171.159', port=22, username='root', password=password)

    for cmd in commands:
        print(f"\n{'='*60}")
        print(f"CMD: {cmd[:120]}")
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
psql = 'docker exec regent-postgres psql -U regent -d regent -t -c'

commands = [
    # Goal status distribution
    f"""{psql} "SELECT status, count(*) as cnt FROM goals GROUP BY status ORDER BY cnt DESC;" """,

    # Outbox event status
    f"""{psql} "SELECT status, count(*) as cnt FROM outbox_events GROUP BY status ORDER BY cnt DESC;" """,

    # Dead letter events with details
    f"""{psql} "SELECT id, event_type, aggregate_id, created_at, left(error_message, 100) as err FROM outbox_events WHERE status = 'DEAD_LETTER' ORDER BY created_at DESC;" """,

    # PREVIEW_SUCCEEDED goals with gate results
    f"""{psql} "SELECT id, title, gate_result, execution_stage, has_metrics, organization_id FROM goals WHERE status = 'PREVIEW_SUCCEEDED' ORDER BY gate_result;" """,

    # Check worker recent logs (last 20 lines) for errors
    "docker logs regent-worker --tail 20 2>&1",

    # Check for any new UniqueViolation in recent worker logs
    "docker logs regent-worker --since 20m 2>&1 | grep -i 'UniqueViolation' | head -5",

    # Check for any errors in API
    "docker logs regent-api --tail 10 2>&1",
]

run_ssh(commands, password)
