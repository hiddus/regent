"""Check generated app quality on server."""
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
    return o.read().decode().strip()

# Find workspace snapshots
print("=== Workspace Snapshots ===")
print(q("SELECT id, workspace_locator, file_count, total_bytes FROM workspace_snapshots ORDER BY created_at DESC LIMIT 3"))

# Check the actual workspace files
print("\n=== Generated Workspace Files ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/workspaces -type f -not -name '.regent-*' 2>/dev/null | head -30", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Show index.html content
print("\n=== index.html content (first 120 lines) ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/workspaces -name 'index.html' -exec head -120 {} \\; 2>/dev/null | head -120", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Show CSS
print("\n=== CSS files ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/workspaces -name '*.css' -exec wc -l {} \\; 2>/dev/null", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Show all generated source files with sizes
print("\n=== All generated files with sizes ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/workspaces -type f -not -name '.regent-*' -exec ls -la {} \\; 2>/dev/null | head -30", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Check preview deployment
print("\n=== Preview endpoint ===")
print(q("SELECT d.endpoint, d.status FROM deployments d ORDER BY d.created_at DESC LIMIT 1"))

# Fetch the actual preview HTML
print("\n=== Preview HTML (curl first 80 lines) ===")
_, stdout, _ = client.exec_command("curl -s http://localhost:8000/previews/ 2>/dev/null | head -5", timeout=15)
out = stdout.read().decode().strip()
print(out or "(none)")

# Get latest goal's preview
print("\n=== Preview routes ===")
_, stdout, _ = client.exec_command("docker exec regent-api ls /var/lib/regent/previews/ 2>/dev/null", timeout=15)
print(stdout.read().decode().strip() or "(none)")

_, stdout, _ = client.exec_command("find /var/lib/regent/previews -name 'index.html' -exec head -100 {} \\; 2>/dev/null | head -100", timeout=30)
print("\n=== Preview index.html ===")
print(stdout.read().decode().strip() or "(none)")

client.close()
