#!/usr/bin/env python3
"""Check dead letter goals and replay them by resetting to PENDING."""
import paramiko
import json

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

# Check the 4 replayable dead letter goals
dead_letter_goal_ids = [
    'b051c3e5-4b3a-4e64-9d32-dd4457d0dcb1',  # UniqueViolation #1
    '7aeb0c18-52a7-410b-83dd-8678c67ed260',   # UniqueViolation #2
    '2c3a3e77-09c8-4026-ba8e-f4d0ee283306',   # no metric definitions bound #1
    '9c0088b4-06f2-42ad-b5fd-4bc790354e95',   # no metric definitions bound #2
]

# Build a query to check all 4 goals
goal_ids_str = "','".join(dead_letter_goal_ids)

commands = [
    # Check these 4 goals' status and execution_stage
    f"""{psql} "SELECT id, status, app_project_id, metadata->>'execution_stage' as exec_stage, metadata->>'last_gate_status' as gate, metadata->>'last_deployment_id' as deploy_id, metadata->>'last_preview_endpoint' as preview_url FROM goals WHERE id IN ('{goal_ids_str}');" """,

    # Check full metadata for the UniqueViolation goal
    f"""{psql} "SELECT jsonb_pretty(metadata) FROM goals WHERE id = 'b051c3e5-4b3a-4e64-9d32-dd4457d0dcb1';" """,

    # Check full metadata for the no-metrics-bound goal
    f"""{psql} "SELECT jsonb_pretty(metadata) FROM goals WHERE id = '2c3a3e77-09c8-4026-ba8e-f4d0ee283306';" """,

    # Check the full payload for the dead letter events
    f"""{psql} "SELECT id, event_type, payload FROM outbox_events WHERE id IN ('2476f5af-de29-47e4-b7d6-f1297be16bb8','91f1881b-6c87-45e9-8e5a-0464a43e9d5c','1cdaa92c-74db-4756-8b6b-07d8c2b888fa','ff467afc-5d32-4ede-a3d9-3e40f52add29');" """,
]

run_ssh(commands, password)
