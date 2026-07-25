"""Debug app builds and deployments."""
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


# Check app_builds
print("=== App Builds ===")
out = run_cmd(f'{PSQL} -c "SELECT column_name FROM information_schema.columns WHERE table_name=\'app_builds\' ORDER BY ordinal_position"')
print(f"Columns: {out}")

out = run_cmd(f'{PSQL} -c "SELECT * FROM app_builds ORDER BY created_at DESC LIMIT 5"')
print(f"Data: {out}")

# Check deployments
print("\n=== Deployments ===")
out = run_cmd(f'{PSQL} -c "SELECT column_name FROM information_schema.columns WHERE table_name=\'deployments\' ORDER BY ordinal_position"')
print(f"Columns: {out}")

out = run_cmd(f'{PSQL} -c "SELECT * FROM deployments ORDER BY created_at DESC LIMIT 5"')
print(f"Data: {out}")

# Check release candidates
print("\n=== Release Candidates ===")
out = run_cmd(f'{PSQL} -c "SELECT * FROM release_candidates ORDER BY created_at DESC LIMIT 5"')
print(f"Data: {out}")

# Check app_preview_releases
print("\n=== App Preview Releases ===")
out = run_cmd(f'{PSQL} -c "SELECT * FROM app_preview_releases ORDER BY created_at DESC LIMIT 5"')
print(f"Data: {out}")

# Check the file content that was generated
print("\n=== Latest File Change Set Content ===")
out = run_cmd(f'{PSQL} -c "SELECT LEFT(content_json::text, 2000) FROM file_change_sets ORDER BY created_at DESC LIMIT 1"')
print(out)

client.close()
