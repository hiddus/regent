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

print("=== App Builds ===")
out = run_cmd(PSQL + ' -c "SELECT id, status, build_artifact_uri FROM app_builds ORDER BY created_at DESC LIMIT 5"')
print(out if out else "(empty)")

print()
print("=== Deployments ===")
out = run_cmd(PSQL + ' -c "SELECT id, status, failure_reason FROM deployments ORDER BY created_at DESC LIMIT 5"')
print(out if out else "(empty)")

print()
print("=== Release Candidates ===")
out = run_cmd(PSQL + ' -c "SELECT id, status FROM release_candidates ORDER BY created_at DESC LIMIT 5"')
print(out if out else "(empty)")

print()
print("=== Latest File Change Set ===")
out = run_cmd(PSQL + ' -c "SELECT LEFT(content_json::text, 1500) FROM file_change_sets ORDER BY created_at DESC LIMIT 1"')
print(out if out else "(empty)")

client.close()
