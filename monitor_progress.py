#!/usr/bin/env python3
"""Monitor new goal execution and overall progress."""
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

print(">>> Waiting 60 seconds for execution chain to progress...")
time.sleep(60)

commands = [
    # Check the new goal's execution progress
    f"""{psql} "SELECT id, status, version, metadata->>'execution_stage' as exec_stage, metadata->>'execution_event_id' as exec_event, metadata->>'problem' as problem FROM goals WHERE id = '6a286e4e-b049-457e-81e4-5a105c94ed06';" """,

    # Check outbox events for this goal
    f"""{psql} "SELECT id, event_type, status, left(last_error, 80) as err FROM outbox_events WHERE aggregate_id = '6a286e4e-b049-457e-81e4-5a105c94ed06' ORDER BY occurred_at DESC LIMIT 15;" """,

    # Check REVISE goals progress
    f"""{psql} "SELECT id, status, metadata->>'execution_stage' as exec_stage, metadata->>'last_revise_discovery_round_id' as round_id FROM goals WHERE id IN ('2c3a3e77-09c8-4026-ba8e-f4d0ee283306','9c0088b4-06f2-42ad-b5fd-4bc790354e95');" """,

    # Overall goal status
    f"""{psql} "SELECT status, count(*) FROM goals GROUP BY status ORDER BY count(*) DESC;" """,

    # Outbox status
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,

    # Worker logs - last 20 lines
    "docker logs regent-worker --tail 20 2>&1",
]

run_ssh(commands, password)
