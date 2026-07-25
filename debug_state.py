"""Check latest state after acceptance test."""
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


def qf(sql):
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
    tmp.write(sql)
    tmp.close()
    sftp = client.open_sftp()
    sftp.put(tmp.name, "/tmp/debug.sql")
    sftp.close()
    _, o, e = client.exec_command(f"{PSQL} -f /tmp/debug.sql", timeout=30)
    return o.read().decode().strip()


print("=== Latest Goals ===")
print(q("SELECT id, status, created_at FROM goals ORDER BY created_at DESC LIMIT 5"))

print("\n=== Latest Outbox Events ===")
print(q("SELECT event_type, aggregate_id, LEFT(payload::text, 300) FROM outbox_events ORDER BY occurred_at DESC LIMIT 20"))

print("\n=== Latest Generation Runs ===")
print(q("SELECT id, plan_id, status, failure_code, attempt, correlation_id FROM generation_runs ORDER BY created_at DESC LIMIT 10"))

print("\n=== Latest Generation Plans ===")
print(q("SELECT id, status, version FROM generation_plans ORDER BY created_at DESC LIMIT 10"))

# Check iteration decisions
print("\n=== Latest Iteration Decisions ===")
print(qf("SELECT id, goal_id, decision, reason, milestone_key FROM iteration_decisions ORDER BY created_at DESC LIMIT 10"))

client.close()
