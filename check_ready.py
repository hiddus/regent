#!/usr/bin/env python3
"""Check READY goals and investigate app project confirm flow."""
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
    # Check READY goals
    f"""{psql} "SELECT id, status, app_project_id, jsonb_pretty(metadata) FROM goals WHERE status = 'READY';" """,

    # Check app_projects table
    f"""{psql} "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'app_projects' ORDER BY ordinal_position;" """,

    # Check app_projects status distribution
    f"""{psql} "SELECT status, count(*) FROM app_projects GROUP BY status ORDER BY count(*) DESC;" """,

    # Check the app project that was being polled (from API logs)
    f"""{psql} "SELECT id, status, spec_hash, spec_status, jsonb_pretty(metadata) FROM app_projects WHERE id = 'c5dee144-bb53-4c51-815e-6a3316c59889';" """,

    # Check the app project that had 409 on confirm
    f"""{psql} "SELECT id, status, spec_hash, spec_status FROM app_projects WHERE id = 'dc7c961b-2629-4ed2-94e5-b6e9263d4fa6';" """,

    # Check outbox events for downstream processing status
    f"""{psql} "SELECT id, event_type, status, left(last_error, 80) as err FROM outbox_events WHERE status IN ('PENDING', 'DISPATCHING') ORDER BY status;" """,
]

run_ssh(commands, password)
