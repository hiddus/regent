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

# Step 1: Find the actual main.py path in the API container
out, err = run("docker exec regent-api find /usr/local/lib -name 'main.py' -path '*/regent/api/*' 2>/dev/null")
print("API main.py path:", out.strip())

# Step 2: Backup original main.py
out, err = run("docker exec regent-api cp /usr/local/lib/python3.12/site-packages/regent/api/main.py /usr/local/lib/python3.12/site-packages/regent/api/main.py.bak 2>&1 && echo 'BACKUP_OK'")
print("Backup API:", out.strip())

# Step 3: Copy modified main.py to server, then to API container
sftp = ssh.open_sftp()
sftp.put("C:/regent/core/src/regent/api/main.py", "/tmp/main_new.py")
sftp.close()

out, err = run("docker cp /tmp/main_new.py regent-api:/usr/local/lib/python3.12/site-packages/regent/api/main.py && echo 'COPIED'")
print("Copy to API:", out.strip(), err.strip()[:200])

# Step 4: Restart API container
out, err = run("docker restart regent-api && echo 'RESTARTED'")
print("Restart API:", out.strip())

time.sleep(3)

# Step 5: Test new health endpoint
out, err = run("docker exec regent-api curl -s http://localhost:8000/v1/health 2>&1 || echo 'CURL_NOT_AVAILABLE'")
print("Health endpoint test (curl):", out.strip()[:500])

# Try with python instead
out, err = run("""docker exec regent-api python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get("http://localhost:8000/v1/health")
        print(r.status_code)
        print(r.text[:500])
asyncio.run(t())
' 2>&1""")
print("\nHealth endpoint test (python):", out.strip()[:500])

# Step 6: Deploy reconciler to worker
sftp = ssh.open_sftp()
sftp.put("C:/regent/core/src/regent/infrastructure/run_reconciler.py", "/tmp/run_reconciler.py")
sftp.close()

out, err = run("docker cp /tmp/run_reconciler.py regent-worker:/usr/local/lib/python3.12/site-packages/regent/infrastructure/run_reconciler.py && echo 'COPIED'")
print("\nDeploy reconciler:", out.strip(), err.strip()[:200])

# Step 7: Start reconciler as background process in worker
out, err = run("docker exec -d regent-worker python3 -m regent.infrastructure.run_reconciler 2>&1; echo 'STARTED'")
print("Start reconciler:", out.strip(), err.strip()[:200])

time.sleep(3)

# Step 8: Check if reconciler started
out, err = run("docker exec regent-worker ps aux | grep reconcile | grep -v grep")
print("Reconciler process:", out.strip()[:300] if out.strip() else "NOT RUNNING")

# Step 9: Check logs
out, err = run("docker logs regent-worker --since 10s 2>&1 | tail -10")
print("\n=== WORKER LOGS ===")
print(out.strip()[:500])

# Step 10: Test old health endpoint too
out, err = run("""docker exec regent-api python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get("http://localhost:8000/health/ready")
        print(r.status_code)
        print(r.text[:500])
asyncio.run(t())
' 2>&1""")
print("\n=== HEALTH/READY TEST ===")
print(out.strip()[:500])

ssh.close()
