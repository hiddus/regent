"""Check deployment failure details."""
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

# 1. Deployment record
run("Deployment Record",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, release_candidate_id, status, failure_code, endpoint, evidence FROM deployments WHERE id='2d877d7f-8de6-48aa-ba23-e4f5c62faa52'\"")

# 2. Release candidate
run("Release Candidate",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, goal_id, status, build_id, created_at FROM release_candidates WHERE id IN (SELECT release_candidate_id FROM deployments WHERE id='2d877d7f-8de6-48aa-ba23-e4f5c62faa52')\"")

# 3. Check PreviewDeploymentRequested event payload
run("Preview Deploy Event",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT payload FROM outbox_events WHERE event_type='PreviewDeploymentRequested' AND aggregate_id='5aa76d31-d36f-46ee-8754-2d20d15becbc'\"")

# 4. Worker logs - more detail
run("Worker Logs (10min)", "docker logs regent-worker --since 10m 2>&1 | grep -i -E 'deploy|preview|fail|error' | tail -30")

# 5. Check app_preview_releases
run("Preview Releases",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT column_name FROM information_schema.columns WHERE table_name='app_preview_releases' ORDER BY ordinal_position\"")

# 6. Check recent preview releases
run("Recent Preview Releases",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT * FROM app_preview_releases ORDER BY created_at DESC LIMIT 3\"")

client.close()
