#!/usr/bin/env python3
"""Check post-replay state of the 4 goals and new outbox events."""
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

goal_ids = [
    'b051c3e5-4b3a-4e64-9d32-dd4457d0dcb1',
    '7aeb0c18-52a7-410b-83dd-8678c67ed260',
    '2c3a3e77-09c8-4026-ba8e-f4d0ee283306',
    '9c0088b4-06f2-42ad-b5fd-4bc790354e95',
]
goals_str = "','".join(goal_ids)

commands = [
    # Check goal status and metadata after replay
    f"""{psql} "SELECT id, status, metadata->>'execution_stage' as exec_stage, metadata->>'last_gate_status' as gate, metadata->>'last_iteration_decision' as decision, metadata->>'last_deployment_id' as deploy_id FROM goals WHERE id IN ('{goals_str}');" """,

    # Check outbox event status
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,

    # Check for new PENDING events (created by the replay processing)
    f"""{psql} "SELECT id, event_type, aggregate_id, status, occurred_at FROM outbox_events WHERE status IN ('PENDING', 'DISPATCHING') ORDER BY occurred_at DESC LIMIT 20;" """,

    # Check recent DISPATCHED events (last 10)
    f"""{psql} "SELECT id, event_type, aggregate_id, status, occurred_at FROM outbox_events WHERE status = 'DISPATCHED' ORDER BY occurred_at DESC LIMIT 10;" """,

    # Check worker logs for latest activity
    "docker logs regent-worker --tail 30 2>&1",

    # Check new metric bindings for these goals
    f"""{psql} "SELECT goal_id, deployment_id, metric_key, definition_version FROM metric_definition_bindings WHERE goal_id IN ('{goals_str}') ORDER BY goal_id, deployment_id, metric_key;" """,
]

run_ssh(commands, password)
