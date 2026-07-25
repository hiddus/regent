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

GOAL_ID = "4009a4f0-d206-419f-90a4-cc9f5b9d62cb"

print("=== Latest Deployment ===")
out = run_cmd(PSQL + f' -c "SELECT id, status, evidence FROM deployments WHERE correlation_id = (SELECT correlation_id FROM goals WHERE id=\'{GOAL_ID}\') ORDER BY created_at DESC LIMIT 1"')
print(out if out else "(empty)")

print("\n=== Outbox Events ===")
out = run_cmd(PSQL + f' -c "SELECT event_type FROM outbox_events WHERE aggregate_id=\'{GOAL_ID}\' ORDER BY occurred_at"')
print(out if out else "(empty)")

print("\n=== Worker Logs (last 50) ===")
out = run_cmd("docker logs regent-worker --tail 50 2>&1")
print(out)

client.close()
