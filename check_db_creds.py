#!/usr/bin/env python3
"""Check DB credentials and container env."""
import paramiko

def run_ssh(commands, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('118.31.171.159', port=22, username='root', password=password)

    for cmd in commands:
        print(f"\n{'='*60}")
        print(f"CMD: {cmd}")
        print('='*60)
        stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        if out:
            print(out)
        if err:
            print(f"STDERR: {err}")

    client.close()

password = '080900.UI'

commands = [
    # Check postgres container env for DB credentials
    "docker exec regent-postgres env | grep -i postgres",
    # Check API container env for DATABASE_URL
    "docker exec regent-api env | grep -i database",
    # Check all container names
    "docker ps --format '{{.Names}} {{.Status}}'",
    # Try with regent user
    """docker exec regent-postgres psql -U regent -d regent -t -c "SELECT current_user, current_database();" """,
    # Check the constraint that currently exists
    """docker exec regent-postgres psql -U regent -d regent -t -c "
    SELECT con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'metric_definition_bindings'
    AND con.contype = 'u';
    " """,
]

run_ssh(commands, password)
