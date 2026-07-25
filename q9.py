import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

def run_cmd(cmd):
    _, o, e = client.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

GOAL_ID = "92230b7f-618c-4c38-9229-89db7906db07"

print("=== Goal Status ===")
out = run_cmd(PSQL + f' -c "SELECT id, status, metadata_json::text FROM goals WHERE id=\'{GOAL_ID}\'"')
print(out[:2000] if out else "(empty)")

print("\n=== Outbox Events ===")
out = run_cmd(PSQL + f' -c "SELECT event_type FROM outbox_events WHERE aggregate_id=\'{GOAL_ID}\' ORDER BY occurred_at"')
print(out if out else "(empty)")

print("\n=== Deployments ===")
out = run_cmd(PSQL + f' -c "SELECT id, status, endpoint FROM deployments WHERE correlation_id = (SELECT correlation_id FROM goals WHERE id=\'{GOAL_ID}\')"')
print(out if out else "(empty)")

print("\n=== Worker Logs (last 30) ===")
out = run_cmd("docker logs regent-worker --tail 30 2>&1")
print(out)

client.close()
