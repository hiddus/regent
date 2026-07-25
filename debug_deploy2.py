"""Debug deploy failure - v2."""
import paramiko
import json

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

CORR_ID = "c7c83c72-460a-4ff2-9e0a-fc16f6425072"
GOAL_ID = "ff293ab0-768c-4121-9dd1-7a954e1f760e"


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


# Query outbox by aggregate_id = goal_id
print("=== Outbox Events (by goal_id) ===")
print(qf(f"SELECT event_type, LEFT(payload::text, 500) FROM outbox_events WHERE aggregate_id='{GOAL_ID}' ORDER BY occurred_at"))

# Check generation runs by correlation_id
print("\n=== Generation Runs (by corr) ===")
print(qf(f"SELECT id, plan_id, status, failure_code, attempt FROM generation_runs WHERE correlation_id='{CORR_ID}' ORDER BY attempt"))

# Check file change sets
print("\n=== File Change Sets ===")
run_ids = qf(f"SELECT id FROM generation_runs WHERE correlation_id='{CORR_ID}'")
if run_ids:
    for rid in run_ids.split("\n"):
        rid = rid.strip()
        if rid:
            print(f"Run {rid}:")
            print(qf(f"SELECT id, generator_ref, LEFT(content_json::text, 600) FROM file_change_sets WHERE generation_run_id='{rid}'"))

# Check workspace snapshots
print("\n=== Workspace Snapshots ===")
if run_ids:
    for rid in run_ids.split("\n"):
        rid = rid.strip()
        if rid:
            print(qf(f"SELECT id, file_count, total_bytes, manifest_uri FROM workspace_snapshots WHERE generation_run_id='{rid}'"))

# Check release candidates and deployments by looking at all recent ones
print("\n=== Recent Deployments ===")
print(qf("SELECT id, status, environment, failure_reason, correlation_id, created_at FROM deployments ORDER BY created_at DESC LIMIT 5"))

print("\n=== Recent Release Candidates ===")
print(qf("SELECT id, status, build_artifact_uri, correlation_id FROM release_candidates ORDER BY created_at DESC LIMIT 5"))

# Check iteration decisions
print("\n=== Iteration Decisions ===")
print(qf(f"SELECT id, decision, reason, milestone_key FROM iteration_decisions WHERE goal_id='{GOAL_ID}' ORDER BY created_at"))

# Check goal metadata
print("\n=== Goal Metadata ===")
print(qf(f"SELECT metadata_json FROM goals WHERE id='{GOAL_ID}'"))

client.close()
