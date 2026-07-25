"""Debug generation run failures."""
import paramiko
import json

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

GOAL_ID = "622ad66b-3d2a-442a-82f3-570baaedd6f8"


def q(sql):
    _, o, e = client.exec_command(f'{PSQL} -c "{sql}"', timeout=30)
    return o.read().decode().strip()


def qf(sql):
    """Query using file-based approach for complex SQL."""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
    tmp.write(sql)
    tmp.close()
    sftp = client.open_sftp()
    sftp.put(tmp.name, "/tmp/debug.sql")
    sftp.close()
    _, o, e = client.exec_command(f"{PSQL} -f /tmp/debug.sql", timeout=30)
    return o.read().decode().strip()


print("=== Gen Run columns ===")
print(q("SELECT column_name FROM information_schema.columns WHERE table_name='generation_runs' ORDER BY ordinal_position"))

print("\n=== All Gen Runs ===")
print(qf(f"SELECT id, plan_id, status, failure_code, attempt FROM generation_runs WHERE correlation_id='{GOAL_ID}' ORDER BY attempt"))

print("\n=== Gen Plans ===")
print(qf(f"""SELECT gp.id, gp.status, gp.version 
FROM generation_plans gp 
JOIN requirement_revisions rr ON gp.requirement_revision_id = rr.id 
WHERE rr.goal_id='{GOAL_ID}'"""))

# Check outbox events for generation related payloads
print("\n=== Generation Related Outbox Events ===")
print(qf(f"SELECT event_type, LEFT(payload::text, 500) FROM outbox_events WHERE aggregate_id='{GOAL_ID}' AND event_type LIKE '%GENERATION%' ORDER BY occurred_at"))

# Check API logs for errors
print("\n=== API error logs ===")
_, o, e = client.exec_command("docker logs regent-api --tail 100 2>&1 | grep -i -A3 'error\\|exception\\|traceback\\|fail'", timeout=30)
print(o.read().decode())

# Check worker logs for errors
print("\n=== Worker error logs ===")
_, o, e = client.exec_command("docker logs regent-worker --tail 500 2>&1 | grep -i -A5 'error\\|exception\\|traceback\\|fail'", timeout=30)
print(o.read().decode())

client.close()
