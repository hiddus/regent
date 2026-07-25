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

GOAL_ID = "4009a4f0-d206-419f-90a4-cc9f5b9d62cb"

# Get correlation_id
print("=== Goal correlation_id ===")
corr = run_cmd(PSQL + f' -c "SELECT correlation_id FROM goals WHERE id=\'{GOAL_ID}\'"')
print(f"corr={corr}")

# Check all recent deployments
print("\n=== All Recent Deployments ===")
out = run_cmd(PSQL + ' -c "SELECT id, status, correlation_id, evidence::text FROM deployments ORDER BY created_at DESC LIMIT 5"')
print(out if out else "(empty)")

# Check release candidates
print("\n=== Recent Release Candidates ===")
out = run_cmd(PSQL + ' -c "SELECT id, status, correlation_id FROM release_candidates ORDER BY created_at DESC LIMIT 5"')
print(out if out else "(empty)")

# Check app builds for this correlation
if corr:
    print(f"\n=== Deployments for corr={corr} ===")
    out = run_cmd(PSQL + f' -c "SELECT id, status, evidence::text FROM deployments WHERE correlation_id=\'{corr}\'"')
    print(out if out else "(empty)")

# Check file change set content
print("\n=== Latest Generated HTML Content ===")
out = run_cmd(PSQL + ' -c "SELECT content_json->\'changes\'->3->>\'relative_path\' FROM file_change_sets ORDER BY created_at DESC LIMIT 1"')
print(f"4th file: {out}")

# Check the actual artifact content
print("\n=== Generated Files List ===")
out = run_cmd(PSQL + ' -c "SELECT jsonb_array_elements(content_json->\'changes\')->>\'relative_path\' FROM file_change_sets ORDER BY created_at DESC LIMIT 1"')
print(out if out else "(empty)")

client.close()
