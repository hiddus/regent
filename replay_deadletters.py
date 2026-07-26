#!/usr/bin/env python3
"""Replay 4 PreviewDeploymentSucceeded dead letters by resetting to PENDING."""
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

# The 4 PreviewDeploymentSucceeded dead letters to replay
dead_letter_ids = [
    '2476f5af-de29-47e4-b7d6-f1297be16bb8',  # UniqueViolation #1 (goal b051c3e5)
    '91f1881b-6c87-45e9-8e5a-0464a43e9d5c',  # UniqueViolation #2 (goal 7aeb0c18)
    '1cdaa92c-74db-4756-8b6b-07d8c2b888fa',  # no metrics bound #1 (goal 2c3a3e77)
    'ff467afc-5d32-4ede-a3d9-3e40f52add29',  # no metrics bound #2 (goal 9c0088b4)
]

ids_str = "','".join(dead_letter_ids)

commands = [
    # First check current state of these events
    f"""{psql} "SELECT id, event_type, status, attempt, left(last_error, 80) as err FROM outbox_events WHERE id IN ('{ids_str}');" """,

    # Check if there are existing metric bindings for these goals (to understand what will happen)
    f"""{psql} "SELECT goal_id, deployment_id, metric_key, definition_version FROM metric_definition_bindings WHERE goal_id IN ('b051c3e5-4b3a-4e64-9d32-dd4457d0dcb1','7aeb0c18-52a7-410b-83dd-8678c67ed260','2c3a3e77-09c8-4026-ba8e-f4d0ee283306','9c0088b4-06f2-42ad-b5fd-4bc790354e95') ORDER BY goal_id, deployment_id;" """,

    # Reset the 4 dead letters to PENDING
    f"""{psql} "UPDATE outbox_events SET status = 'PENDING', attempt = 0, last_error = NULL, lease_owner = NULL, lease_expires_at = NULL, available_at = NOW() WHERE id IN ('{ids_str}');" """,

    # Verify the reset
    f"""{psql} "SELECT id, event_type, status, attempt FROM outbox_events WHERE id IN ('{ids_str}');" """,

    # Check PENDING count (should be 4)
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,
]

run_ssh(commands, password)

print("\n\n>>> Waiting 15 seconds for worker to pick up PENDING events...")
time.sleep(15)

# Check worker logs for processing
commands2 = [
    # Check worker logs for recent activity
    "docker logs regent-worker --since 30s 2>&1 | tail -40",
    # Check outbox status after worker processing
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,
    # Check if any of the 4 events were processed
    f"""{psql} "SELECT id, event_type, status, attempt, left(last_error, 120) as err FROM outbox_events WHERE id IN ('{ids_str}');" """,
]

run_ssh(commands2, password)
