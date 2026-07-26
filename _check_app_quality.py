"""Check actual generated APP content."""
import paramiko
import zipfile
import io

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("118.31.171.159", username="root", password="080900.UI", timeout=15)

def run(name, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"\n=== {name} ===")
    if out:
        print(out[-5000:] if len(out) > 5000 else out)
    if err and "WARNING" not in err and "DEPRECATED" not in err:
        print(f"STDERR: {err[-1000:]}")

# List build artifacts
run("Build artifacts",
    "ls -la /opt/regent/builds/sandbox/*/output/app-source.zip 2>/dev/null | tail -5")

# Extract and show latest build content
run("Latest build content",
    """python3 -c "
import zipfile, os, glob
zips = sorted(glob.glob('/opt/regent/builds/sandbox/*/output/app-source.zip'), key=os.path.getmtime, reverse=True)
if zips:
    with zipfile.ZipFile(zips[0]) as z:
        for name in z.namelist()[:10]:
            content = z.read(name).decode('utf-8', errors='ignore')
            print(f'=== {name} ({len(content)} chars) ===')
            print(content[:1500])
            print()
" """)

# Check generation plan contract
run("Generation Plan Contract",
    "docker exec regent-postgres psql -U regent -d regent -t -A -c "
    "\"SELECT LEFT(contract_json::text, 1000) FROM generation_plans ORDER BY created_at DESC LIMIT 1\"")

client.close()
