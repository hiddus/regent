"""Check generated app quality on server - v2."""
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
    if err and "NOTICE" not in err:
        print(f"  ERR: {err[:200]}")
    return out

def ssh(cmd):
    _, o, e = client.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

# Check workspace dirs from DB
print("=== Workspace locators ===")
locs = q("SELECT workspace_locator FROM workspace_snapshots ORDER BY created_at DESC LIMIT 3")
print(locs)

# Check if those directories exist
for loc in locs.split("\n"):
    loc = loc.strip()
    if loc:
        print(f"\n=== Files in {loc} ===")
        print(ssh(f"find {loc} -type f 2>/dev/null | head -20"))

# Check the preview service
print("\n=== Preview service routes ===")
print(ssh("docker exec regent-api python -c 'from regent.api.main import app; print([r.path for r in app.routes])' 2>&1 | head -5"))

# Check app_previews table
print("\n=== App Previews ===")
print(q("SELECT id, snapshot_id, status, endpoint FROM app_previews ORDER BY created_at DESC LIMIT 3"))

# Check how preview serves content
print("\n=== Preview API code ===")
print(ssh("grep -n 'def\\|route\\|mount\\|static' /opt/regent/current/core/src/regent/api/app_previews.py 2>/dev/null | head -20"))

# Check the actual generated content via artifact store
print("\n=== Artifact store ===")
print(ssh("find /var/lib/regent/artifacts -type f 2>/dev/null | head -20"))

# Check generation_runs for model info
print("\n=== Generation Runs ===")
print(q("SELECT id, status, model_ref, input_tokens, output_tokens, attempt FROM generation_runs ORDER BY created_at DESC LIMIT 3"))

# Check what the code generator actually produced
print("\n=== File Change Sets ===")
print(q("SELECT id, generation_run_id, generator_ref, prompt_version FROM file_change_sets ORDER BY created_at DESC LIMIT 3"))

# Get the actual file changes
print("\n=== File Changes (latest) ===")
print(q("""SELECT fcs.id, fcj.relative_path, fcj.operation 
FROM file_change_sets fcs 
JOIN LATERAL (
  SELECT elem->>'relative_path' as relative_path, elem->>'operation' as operation
  FROM jsonb_array_elements(fcs.content_json->'changes') as elem
) fcj ON true
WHERE fcs.generation_run_id IN (SELECT id FROM generation_runs ORDER BY created_at DESC LIMIT 1)
ORDER BY fcs.created_at DESC LIMIT 10"""))

client.close()
