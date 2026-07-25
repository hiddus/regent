"""Check deployment failure for goal 5aa76d31."""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("118.31.171.159", username="root", password="080900.UI", timeout=15)

def run(name, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"\n=== {name} ===")
    if out:
        print(out[-5000:] if len(out) > 5000 else out)
    if err and "WARNING" not in err and "DEPRECATED" not in err:
        print(f"STDERR: {err[-1000:]}")

# 1. Deployment details
run("Deployment Details",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, goal_id, status, failure_reason, created_at FROM deployments WHERE id='2d877d7f-8de6-48aa-ba23-e4f5c62faa52'\"")

# 2. Goal metadata
run("Goal Metadata",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, metadata->>'execution_stage' as stage, metadata->>'halt' as halt FROM goals WHERE id='5aa76d31-d36f-46ee-8754-2d20d15becbc'\"")

# 3. Recent outbox events for this goal
run("Recent Outbox Events",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT event_type, status, attempt, last_error, occurred_at FROM outbox_events WHERE aggregate_id='5aa76d31-d36f-46ee-8754-2d20d15becbc' ORDER BY occurred_at DESC LIMIT 10\"")

# 4. Worker logs
run("Worker Logs (5min)", "docker logs regent-worker --since 5m 2>&1 | tail -60")

# 5. Check deployments table schema
run("Deployments Schema",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT column_name FROM information_schema.columns WHERE table_name='deployments' ORDER BY ordinal_position\"")

# 6. Check all deployments for this goal
run("All Deployments",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, goal_id, status, created_at FROM deployments WHERE goal_id='5aa76d31-d36f-46ee-8754-2d20d15becbc' ORDER BY created_at\"")

client.close()
