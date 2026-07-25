"""Check delivery gap reasons - database."""
import paramiko
import json

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

def q(sql):
    _, o, e = client.exec_command(f'{PSQL} -c "{sql}"', timeout=30)
    return o.read().decode().strip()

GOAL_ID = "622ad66b-3d2a-442a-82f3-570baaedd6f8"

print("=== Goal Status ===")
print(q(f"SELECT id, status FROM goals WHERE id='{GOAL_ID}'"))

print("\n=== Generation Runs ===")
print(q(f"SELECT id, status, failure_code, attempt FROM generation_runs WHERE correlation_id='{GOAL_ID}' ORDER BY attempt"))

print("\n=== Latest Generation Run Details ===")
run_id = q(f"SELECT id FROM generation_runs WHERE correlation_id='{GOAL_ID}' ORDER BY attempt DESC LIMIT 1")
print(f"Run ID: {run_id}")

print("\n=== File Change Sets ===")
fcs_id = q(f"SELECT id FROM file_change_sets WHERE generation_run_id='{run_id}'")
print(f"FCS ID: {fcs_id}")

if fcs_id:
    print("\n=== File Changes ===")
    changes_json = q(f"SELECT content_json FROM file_change_sets WHERE id='{fcs_id}'")
    if changes_json:
        try:
            data = json.loads(changes_json)
            changes = data.get('changes', [])
            print(f"Number of files: {len(changes)}")
            for c in changes:
                print(f"  - {c.get('relative_path')} ({c.get('operation')})")
        except Exception as e:
            print(f"Parse error: {e}")

print("\n=== Outbox Events (DELIVERY_GAP) ===")
print(q(f"SELECT event_type, payload FROM outbox_events WHERE aggregate_id='{GOAL_ID}' AND event_type LIKE '%DELIVERY_GAP%' ORDER BY occurred_at DESC LIMIT 3"))

print("\n=== Iteration Decisions ===")
print(q(f"SELECT id, decision, reason FROM iteration_decisions WHERE goal_id='{GOAL_ID}' ORDER BY created_at DESC LIMIT 3"))

client.close()
