"""Debug full event chain."""
import paramiko

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
    return o.read().decode().strip()


# Get ALL outbox events for this goal
print("=== All Outbox Events ===")
out = run_cmd(f'{PSQL} -c "SELECT event_type FROM outbox_events WHERE aggregate_id=\'{GOAL_ID}\' ORDER BY occurred_at"')
print(out)

# Check workspace snapshots
print("\n=== Workspace Snapshots ===")
out = run_cmd(f'{PSQL} -c "SELECT id, file_count, manifest_uri FROM workspace_snapshots ORDER BY created_at DESC LIMIT 5"')
print(out)

# Check build runs (sandbox builds)
print("\n=== Build Runs ===")
out = run_cmd(f'{PSQL} -c "SELECT table_name FROM information_schema.tables WHERE table_schema=\'public\' ORDER BY table_name"')
print(f"Tables: {out}")

# Check for any build-related tables
print("\n=== Sandbox Build Results ===")
out = run_cmd(f'{PSQL} -c "SELECT id, status, artifact_uri FROM sandbox_builds ORDER BY created_at DESC LIMIT 5"')
print(out)

# Check release candidates
print("\n=== Release Candidates ===")
out = run_cmd(f'{PSQL} -c "SELECT id, status, build_artifact_uri FROM release_candidates ORDER BY created_at DESC LIMIT 5"')
print(out)

# Get full worker logs
print("\n=== Full Worker Logs ===")
out = run_cmd("docker logs regent-worker --tail 200 2>&1")
print(out)

client.close()
