#!/usr/bin/env python3
"""Final comprehensive check."""
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
psql = 'docker exec regent-postgres psql -U regent -d regent -t -c'

print(">>> Waiting 45 seconds for more progress...")
time.sleep(45)

commands = [
    # Comprehensive goal status
    f"""{psql} "SELECT status, count(*) as cnt, count(*) FILTER (WHERE metadata->>'execution_stage' IS NOT NULL) as with_stage, count(*) FILTER (WHERE metadata->>'organization_id' IS NOT NULL) as with_org FROM goals GROUP BY status ORDER BY cnt DESC;" """,

    # Execution stage breakdown for ACTIVE
    f"""{psql} "SELECT metadata->>'execution_stage' as exec_stage, count(*) FROM goals WHERE status = 'ACTIVE' GROUP BY exec_stage ORDER BY count(*) DESC;" """,

    # New goal progress
    f"""{psql} "SELECT id, status, version, metadata->>'execution_stage' as stage, metadata->>'current_milestone_ordinal' as milestone, metadata->>'milestone_count' as ms_count FROM goals WHERE id = '6a286e4e-b049-457e-81e4-5a105c94ed06';" """,

    # All outbox events for the new goal
    f"""{psql} "SELECT id, event_type, status, left(last_error, 80) as err FROM outbox_events WHERE aggregate_id = '6a286e4e-b049-457e-81e4-5a105c94ed06' ORDER BY occurred_at DESC;" """,

    # Outbox overall
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,

    # ACHIEVED goals count
    f"""{psql} "SELECT count(*) as achieved FROM goals WHERE status = 'ACHIEVED';" """,

    # Check for organization_id in any goals
    f"""{psql} "SELECT id, status, metadata->>'organization_id' as org_id, metadata->>'organization_strategy' as org_strategy FROM goals WHERE metadata->>'organization_id' IS NOT NULL;" """,

    # Worker logs - check for errors and organization
    "docker logs regent-worker --tail 30 2>&1 | grep -E 'ERROR|organization|discovery|milestone|ACHIEV|EXHAUST|state change' | tail -15",
]

run_ssh(commands, password)
