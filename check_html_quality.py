"""Check actual generated HTML quality."""
import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)

# Get the latest workspace HTML
print("=== Latest generated index.html ===")
_, stdout, _ = client.exec_command(
    "cat /opt/regent/workspaces/32e2163a-0dc0-4233-a748-42b96a522ed3/src/index.html 2>/dev/null",
    timeout=30
)
html_content = stdout.read().decode().strip()
print(html_content[:3000] if html_content else "(empty)")
print(f"\n... total {len(html_content)} chars")

# Get the app.py
print("\n=== Latest generated app.py ===")
_, stdout, _ = client.exec_command(
    "cat /opt/regent/workspaces/32e2163a-0dc0-4233-a748-42b96a522ed3/src/app.py 2>/dev/null",
    timeout=30
)
py_content = stdout.read().decode().strip()
print(py_content[:2000] if py_content else "(empty)")
print(f"\n... total {len(py_content)} chars")

# Check the file_change_sets content_json for actual generated content
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"
_, stdout, _ = client.exec_command(
    f'''{PSQL} -c "SELECT content_json FROM file_change_sets WHERE id='acfde991-7137-4174-b86b-d98837a0c4e4'"''',
    timeout=30
)
import json
try:
    data = json.loads(stdout.read().decode().strip())
    changes = data.get("changes", [])
    print(f"\n=== File Change Set: {len(changes)} files ===")
    for c in changes:
        path = c.get("relative_path", "?")
        op = c.get("operation", "?")
        uri = c.get("content_artifact_uri", "")
        print(f"  {op}: {path} -> {uri[:80]}")
except Exception as e:
    print(f"Parse error: {e}")

client.close()
