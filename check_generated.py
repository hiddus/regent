"""Check generated app quality."""
import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)

# Find generated files
print("=== Generated Preview Files ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/previews -type f -name '*.html' -o -name '*.css' -o -name '*.py' 2>/dev/null | head -20", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Show latest index.html content
print("\n=== Latest index.html (first 80 lines) ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/previews -name index.html -exec cat {} \\; 2>/dev/null | head -80", timeout=30)
out = stdout.read().decode().strip()
print(out if out else "(none)")

# Show any CSS
print("\n=== Generated CSS ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/previews -name '*.css' -exec cat {} \\; 2>/dev/null | head -40", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Show any Python files
print("\n=== Generated Python ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/previews -name '*.py' -exec cat {} \\; 2>/dev/null | head -40", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Check workspace artifacts
print("\n=== Workspace Snapshots ===")
_, stdout, _ = client.exec_command("ls -la /var/lib/regent/workspaces/ 2>/dev/null | head -10", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Check generation artifacts
print("\n=== Generation Artifacts ===")
_, stdout, _ = client.exec_command("find /var/lib/regent/artifacts -name '*.json' -path '*/generation/*' 2>/dev/null | head -10", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Check file_change_sets
print("\n=== File Change Sets (latest) ===")
_, stdout, _ = client.exec_command("find /var/lib/regent -name 'file_change_set*' -o -name 'generated_*' 2>/dev/null | head -10", timeout=30)
print(stdout.read().decode().strip() or "(none)")

# Check the actual preview endpoint
print("\n=== Preview HTTP Response ===")
_, stdout, _ = client.exec_command("curl -s http://localhost:8000/preview/ 2>/dev/null | head -20", timeout=30)
print(stdout.read().decode().strip() or "(no preview root)")

client.close()
