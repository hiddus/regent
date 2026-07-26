#!/usr/bin/env python3
"""Check table schemas and detailed status."""
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
    # Goals table columns
    f"""{psql} "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'goals' ORDER BY ordinal_position;" """,

    # Outbox events table columns
    f"""{psql} "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'outbox_events' ORDER BY ordinal_position;" """,

    # Goal status + execution_stage distribution
    f"""{psql} "SELECT status, execution_stage, count(*) as cnt FROM goals GROUP BY status, execution_stage ORDER BY status, execution_stage;" """,

    # Goals with gate_result breakdown
    f"""{psql} "SELECT status, gate_result, count(*) as cnt FROM goals GROUP BY status, gate_result ORDER BY status, gate_result;" """,
]

run_ssh(commands, password)
