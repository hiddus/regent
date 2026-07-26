import paramiko, time

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def run(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

# Start reconciler with nohup inside the worker container
out, err = run("""docker exec -d regent-worker bash -c '
cd /usr/local/lib/python3.12/site-packages
RECONCILE_INTERVAL_SECONDS=300
nohup python3 -m regent.infrastructure.run_reconciler > /var/log/regent-reconciler.log 2>&1 &
echo $! > /var/run/regent-reconciler.pid
' 2>&1""")
print("Start:", out.strip(), err.strip()[:200])

time.sleep(3)

# Check if process is running
out, err = run("docker exec regent-worker ps aux | grep reconcile | grep -v grep")
print("Process:", out.strip()[:300] if out.strip() else "NOT RUNNING")

# Check log
out, err = run("docker exec regent-worker cat /var/log/regent-reconciler.log 2>&1 | tail -10")
print("\n=== RECONCILER LOG ===")
print(out.strip()[:500])

# Verify health endpoint still working
out, err = run("""docker exec regent-api python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get("http://localhost:8000/v1/health")
        print("Health:", r.json())
asyncio.run(t())
' 2>&1""")
print("\n=== HEALTH ENDPOINT ===")
print(out.strip()[:500])

ssh.close()
