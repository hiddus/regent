import paramiko
import sys

print("Starting...", flush=True)

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {SERVER}...", flush=True)
    client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
    print("Connected!", flush=True)
    
    PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

    def run_cmd(cmd):
        print(f"Running: {cmd[:80]}...", flush=True)
        _, o, e = client.exec_command(cmd, timeout=30)
        out = o.read().decode().strip()
        err = e.read().decode().strip()
        if err:
            print(f"ERR: {err}", flush=True)
        return out

    print("\n=== App Builds ===", flush=True)
    out = run_cmd(PSQL + ' -c "SELECT id, status, build_artifact_uri FROM app_builds ORDER BY created_at DESC LIMIT 5"')
    print(out if out else "(empty)", flush=True)

    print("\n=== Deployments ===", flush=True)
    out = run_cmd(PSQL + ' -c "SELECT id, status, failure_reason FROM deployments ORDER BY created_at DESC LIMIT 5"')
    print(out if out else "(empty)", flush=True)

    print("\n=== Latest File Change Set ===", flush=True)
    out = run_cmd(PSQL + ' -c "SELECT LEFT(content_json::text, 2000) FROM file_change_sets ORDER BY created_at DESC LIMIT 1"')
    print(out if out else "(empty)", flush=True)

    client.close()
    print("\nDone.", flush=True)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()
