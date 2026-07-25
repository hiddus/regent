"""Debug deploy failure."""
import paramiko
import json

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

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


print("=== Goal ===")
print(q(f"SELECT id, status, correlation_id FROM goals WHERE id='{GOAL_ID}'"))

print("\n=== Outbox Events ===")
print(qf(f"SELECT event_type, LEFT(payload::text, 400) FROM outbox_events WHERE aggregate_id='{GOAL_ID}' ORDER BY occurred_at"))

print("\n=== Generation Runs ===")
print(qf(f"""SELECT gr.id, gr.status, gr.failure_code, gr.attempt 
FROM generation_runs gr WHERE gr.correlation_id = (SELECT correlation_id FROM goals WHERE id='{GOAL_ID}')"""))

print("\n=== File Change Sets ===")
print(qf(f"""SELECT fcs.id, fcs.generator_ref, fcs.prompt_version, LEFT(fcs.content_json::text, 500)
FROM file_change_sets fcs
JOIN generation_runs gr ON fcs.generation_run_id = gr.id
WHERE gr.correlation_id = (SELECT correlation_id FROM goals WHERE id='{GOAL_ID}')"""))

print("\n=== Workspace Snapshots ===")
print(qf(f"""SELECT ws.id, ws.file_count, ws.total_bytes, ws.manifest_uri
FROM workspace_snapshots ws
JOIN generation_runs gr ON ws.generation_run_id = gr.id
WHERE gr.correlation_id = (SELECT correlation_id FROM goals WHERE id='{GOAL_ID}')"""))

print("\n=== Deployments ===")
print(qf(f"""SELECT d.id, d.status, d.environment, d.failure_reason, d.release_candidate_id
FROM deployments d
WHERE d.correlation_id = (SELECT correlation_id FROM goals WHERE id='{GOAL_ID}')
ORDER BY d.created_at DESC LIMIT 5"""))

print("\n=== Release Candidates ===")
print(qf(f"""SELECT rc.id, rc.status, rc.build_artifact_uri
FROM release_candidates rc
WHERE rc.correlation_id = (SELECT correlation_id FROM goals WHERE id='{GOAL_ID}')
ORDER BY rc.created_at DESC LIMIT 5"""))

print("\n=== Worker Logs (last 100 lines) ===")
_, o, e = client.exec_command("docker logs regent-worker --tail 100 2>&1", timeout=30)
print(o.read().decode())

client.close()
