#!/usr/bin/env python3
"""Check downstream completion and PREVIEW_SUCCEEDED goals."""
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

print(">>> Waiting 30 seconds...")
time.sleep(30)

commands = [
    # Check if downstream events completed
    f"""{psql} "SELECT id, event_type, status, left(last_error, 80) as err FROM outbox_events WHERE id IN ('d5309233-e3d9-4d72-a363-bd40dfebab79','b6a4fe57-355e-4d1d-807f-babc0baec0a1','cae948ce-a56a-409d-add8-ffc3b2c4fb98','83f953cf-f0ba-4f41-a99d-af637a7d8240');" """,

    # Outbox status
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,

    # Check the 28 PREVIEW_SUCCEEDED goals - do they have dead letters?
    f"""{psql} "SELECT g.id, g.metadata->>'last_gate_status' as gate, g.metadata->>'last_iteration_decision' as decision, g.metadata->>'last_deployment_id' as deploy_id, (SELECT count(*) FROM outbox_events o WHERE o.aggregate_id = g.id AND o.status = 'DEAD_LETTER') as dead_letter_count FROM goals g WHERE g.status = 'ACTIVE' AND g.metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED' ORDER BY dead_letter_count DESC, g.metadata->>'last_gate_status';" """,

    # Check all dead letters grouped by goal
    f"""{psql} "SELECT aggregate_id, event_type, count(*) as cnt FROM outbox_events WHERE status = 'DEAD_LETTER' GROUP BY aggregate_id, event_type ORDER BY aggregate_id;" """,

    # Current goal status
    f"""{psql} "SELECT status, count(*) FROM goals GROUP BY status ORDER BY count(*) DESC;" """,

    # Check the 2 REVISE goals' latest status
    f"""{psql} "SELECT id, status, metadata->>'execution_stage' as exec_stage FROM goals WHERE id IN ('2c3a3e77-09c8-4026-ba8e-f4d0ee283306','9c0088b4-06f2-42ad-b5fd-4bc790354e95');" """,

    # Worker recent activity
    "docker logs regent-worker --tail 15 2>&1",
]

run_ssh(commands, password)
