#!/usr/bin/env python3
"""Check current state of the Regent system."""
import paramiko
import json

def run_ssh(commands, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('118.31.171.159', port=22, username='root', password=password)

    results = []
    for cmd in commands:
        print(f"\n{'='*60}")
        print(f"CMD: {cmd}")
        print('='*60)
        stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        if out:
            print(out)
        if err:
            print(f"STDERR: {err}")
        results.append((cmd, out, err))

    client.close()
    return results

password = '080900.UI'

# Comprehensive status check
commands = [
    # Goal status distribution
    """docker exec regent-postgres psql -U postgres -d regent -t -c "
    SELECT status, count(*) as cnt FROM goals GROUP BY status ORDER BY cnt DESC;
    " """,

    # Outbox event status
    """docker exec regent-postgres psql -U postgres -d regent -t -c "
    SELECT status, count(*) as cnt FROM outbox_events GROUP BY status ORDER BY cnt DESC;
    " """,

    # Dead letter events with details
    """docker exec regent-postgres psql -U postgres -d regent -t -c "
    SELECT id, event_type, aggregate_id, created_at, error_message
    FROM outbox_events
    WHERE status = 'DEAD_LETTER'
    ORDER BY created_at DESC;
    " """,

    # PREVIEW_SUCCEEDED goals with gate results
    """docker exec regent-postgres psql -U postgres -d regent -t -c "
    SELECT g.id, g.title, g.gate_result, g.execution_stage, g.has_metrics, g.organization_id
    FROM goals g
    WHERE g.status = 'PREVIEW_SUCCEEDED'
    ORDER BY g.gate_result;
    " """,

    # Check worker logs for recent activity
    """docker logs regent-worker --tail 30 2>&1 | head -30""",
]

run_ssh(commands, password)
