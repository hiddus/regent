"""Check what was actually deployed."""
import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

def q(sql):
    _, o, e = client.exec_command(f'{PSQL} -c "{sql}"', timeout=30)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    return out

# Check deployments
print("=== Deployments ===")
print(q("SELECT id,release_candidate_id,status,environment,endpoint,idempotency_key FROM deployments ORDER BY created_at DESC LIMIT 3"))

# Check release candidates
print("\n=== Release Candidates ===")
print(q("SELECT id,app_build_id,status,content_hash FROM release_candidates ORDER BY created_at DESC LIMIT 3"))

# Check app builds
print("\n=== App Builds (latest) ===")
print(q("SELECT id,status,build_artifact_uri,build_artifact_hash FROM app_builds ORDER BY created_at DESC LIMIT 3"))

# Check workspace snapshots
print("\n=== Workspace Snapshots ===")
print(q("SELECT id,generation_run_id,source_archive_uri,runtime_profile_hash FROM workspace_snapshots ORDER BY created_at DESC LIMIT 3"))

# Check generation runs
print("\n=== Generation Runs ===")
print(q("SELECT id,plan_id,status,attempt FROM generation_runs ORDER BY created_at DESC LIMIT 3"))

# Check generation plans
print("\n=== Generation Plans ===")
print(q("SELECT id,status,input_digest FROM generation_plans ORDER BY created_at DESC LIMIT 3"))

# Check file_change_sets
print("\n=== File Change Sets ===")
print(q("SELECT count(*) FROM file_change_sets"))

# Check the artifacts directory
print("\n=== Artifacts Directory ===")
_, o, _ = client.exec_command("find /var/lib/regent/artifacts -type f 2>/dev/null | head -30", timeout=30)
print(o.read().decode().strip() or "(empty)")

# Check preview directory
print("\n=== Preview Directory ===")
_, o, _ = client.exec_command("find /var/lib/regent/previews -type f 2>/dev/null | head -20", timeout=30)
print(o.read().decode().strip() or "(empty)")

# Check all regent directories
print("\n=== /var/lib/regent structure ===")
_, o, _ = client.exec_command("ls -la /var/lib/regent/ 2>/dev/null", timeout=30)
print(o.read().decode().strip() or "(not found)")

client.close()
