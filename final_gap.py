#!/usr/bin/env python3
"""Final dead letter check and gap summary."""
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
    # All remaining dead letters
    f"""{psql} "SELECT id, event_type, aggregate_id, occurred_at, left(last_error, 100) as err FROM outbox_events WHERE status = 'DEAD_LETTER' ORDER BY occurred_at DESC;" """,

    # 11 CONTINUE + PASSED goals (could be advanced with synthetic events)
    f"""{psql} "SELECT id, metadata->>'last_deployment_id' as deploy_id, metadata->>'last_preview_endpoint' as preview, metadata->>'current_milestone_ordinal' as milestone, metadata->>'milestone_count' as ms_count FROM goals WHERE status = 'ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED' AND metadata->>'last_gate_status' = 'PASSED' AND metadata->>'last_iteration_decision' = 'CONTINUE';" """,

    # 17 INSUFFICIENT_EVIDENCE goals (waiting for timer)
    f"""{psql} "SELECT id, metadata->>'last_deployment_id' as deploy_id, metadata->>'gate_insufficient_since' as since, metadata->>'gate_insufficient_timer_id' as timer_id FROM goals WHERE status = 'ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED' AND metadata->>'last_gate_status' = 'INSUFFICIENT_EVIDENCE' LIMIT 5;" """,

    # Count the 11 CONTINUE goals with deploy_id
    f"""{psql} "SELECT count(*) as cont_with_deploy FROM goals WHERE status = 'ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED' AND metadata->>'last_gate_status' = 'PASSED' AND metadata->>'last_iteration_decision' = 'CONTINUE' AND metadata->>'last_deployment_id' IS NOT NULL;" """,
]

run_ssh(commands, password)
