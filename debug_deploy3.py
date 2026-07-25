"""Debug deploy failure - v3."""
import paramiko
import json
import sys

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

GOAL_ID = "ff293ab0-768c-4121-9dd1-7a954e1f760e"


def run_cmd(cmd):
    _, o, e = client.exec_command(cmd, timeout=30)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    return out, err


# Simple queries
print("=== Goal Status ===")
out, err = run_cmd(f'{PSQL} -c "SELECT id, status, correlation_id FROM goals WHERE id=\'{GOAL_ID}\'"')
print(f"OUT: {out}")
if err:
    print(f"ERR: {err}")

print("\n=== Outbox Events ===")
out, err = run_cmd(f'{PSQL} -c "SELECT event_type, payload::text FROM outbox_events WHERE aggregate_id=\'{GOAL_ID}\' ORDER BY occurred_at LIMIT 20"')
print(f"OUT: {out[:3000]}")
if err:
    print(f"ERR: {err}")

print("\n=== Generation Runs ===")
out, err = run_cmd(f'{PSQL} -c "SELECT id, status, failure_code FROM generation_runs ORDER BY created_at DESC LIMIT 5"')
print(f"OUT: {out}")

print("\n=== Deployments ===")
out, err = run_cmd(f'{PSQL} -c "SELECT id, status, failure_reason FROM deployments ORDER BY created_at DESC LIMIT 5"')
print(f"OUT: {out}")

print("\n=== Goal Metadata ===")
out, err = run_cmd(f'{PSQL} -c "SELECT metadata_json FROM goals WHERE id=\'{GOAL_ID}\'"')
print(f"OUT: {out[:2000]}")

print("\n=== Worker Logs (errors) ===")
out, err = run_cmd("docker logs regent-worker --tail 50 2>&1 | grep -i 'error\\|exception\\|fail\\|deploy'")
print(f"OUT: {out}")

client.close()
print("\nDone.")
