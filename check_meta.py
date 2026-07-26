#!/usr/bin/env python3
"""Check detailed status with correct column names."""
import paramiko

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
psql = 'docker exec regent-postgres psql -U regent -d regent -t -c'

commands = [
    # Dead letter events with correct columns
    f"""{psql} "SELECT id, event_type, aggregate_id, occurred_at, left(last_error, 120) as err FROM outbox_events WHERE status = 'DEAD_LETTER' ORDER BY occurred_at DESC;" """,

    # Goal metadata sample (one goal from each status)
    f"""{psql} "SELECT id, status, app_project_id, jsonb_pretty(metadata) as meta FROM goals WHERE status = 'ACTIVE' LIMIT 1;" """,

    # Check metadata keys across all goals
    f"""{psql} "SELECT status, jsonb_object_keys(metadata) as meta_key, count(*) FROM goals GROUP BY status, meta_key ORDER BY status, meta_key;" """,

    # Goal with execution_stage in metadata
    f"""{psql} "SELECT metadata->>'execution_stage' as exec_stage, status, count(*) FROM goals GROUP BY exec_stage, status ORDER BY status, exec_stage;" """,

    # Goals with gate_result in metadata
    f"""{psql} "SELECT metadata->>'gate_result' as gate_result, status, count(*) FROM goals GROUP BY gate_result, status ORDER BY status, gate_result;" """,
]

run_ssh(commands, password)
