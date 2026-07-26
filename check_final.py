#!/usr/bin/env python3
"""Final status check after replay processing completes."""
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

print(">>> Waiting 20 seconds for worker to process downstream events...")
time.sleep(20)

commands = [
    # Overall goal status distribution
    f"""{psql} "SELECT status, count(*) as cnt FROM goals GROUP BY status ORDER BY cnt DESC;" """,

    # Execution stage distribution for ACTIVE goals
    f"""{psql} "SELECT metadata->>'execution_stage' as exec_stage, count(*) FROM goals WHERE status = 'ACTIVE' GROUP BY exec_stage ORDER BY count(*) DESC;" """,

    # Check the 4 new downstream events
    f"""{psql} "SELECT id, event_type, aggregate_id, status, left(last_error, 100) as err FROM outbox_events WHERE id IN ('d5309233-e3d9-4d72-a363-bd40dfebab79','b6a4fe57-355e-4d1d-807f-babc0baec0a1','cae948ce-a56a-409d-add8-ffc3b2c4fb98','83f953cf-f0ba-4f41-a99d-af637a7d8240');" """,

    # Outbox status
    f"""{psql} "SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;" """,

    # Check the 2 REVISE goals' updated metadata
    f"""{psql} "SELECT id, status, metadata->>'execution_stage' as exec_stage, metadata->>'last_revise_discovery_round_id' as round_id FROM goals WHERE id IN ('2c3a3e77-09c8-4026-ba8e-f4d0ee283306','9c0088b4-06f2-42ad-b5fd-4bc790354e95');" """,

    # Check worker logs for recent processing
    "docker logs regent-worker --tail 25 2>&1",

    # Check total ACHIEVED goals (should be 12 now)
    f"""{psql} "SELECT count(*) as achieved_count FROM goals WHERE status = 'ACHIEVED';" """,
]

run_ssh(commands, password)
