import paramiko

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def ssh_cmd(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

# Step 1: Check Squid process
out, err = ssh_cmd("docker exec regent-egress ps aux 2>&1")
print("=== SQUID PROCESS ===")
print(out)

out, err = ssh_cmd("docker exec regent-egress cat /var/run/squid.pid 2>&1; echo '---'; docker exec regent-egress ls /var/run/ 2>&1")
print("=== PID FILE ===")
print(out)

# Step 2: Fix config using awk (more reliable than sed in docker exec)
cmd = """docker exec regent-egress sh -c "awk '{sub(/http_access allow localnet evidence_domains/, \"http_access allow localnet\"); print}' /etc/squid/squid.conf > /tmp/squid_new.conf && mv /tmp/squid_new.conf /etc/squid/squid.conf && echo 'REPLACED'" """
out, err = ssh_cmd(cmd)
print("Replace:", out.strip(), err.strip()[:200])

# Step 3: Verify
out, err = ssh_cmd("docker exec regent-egress grep -n 'http_access allow' /etc/squid/squid.conf")
print("=== NEW ACL ===")
print(out)

# Step 4: Start Squid
out, err = ssh_cmd("docker exec regent-egress squid -N -d1 2>&1 &")
print("Squid start:", out.strip()[:300])

# Step 5: Wait and check
import time
time.sleep(2)
out, err = ssh_cmd("docker exec regent-egress ps aux 2>&1 | grep squid")
print("Squid process after start:", out.strip())

# Step 6: Test proxy
test_cmd = """docker exec regent-worker python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=10) as c:
        r = await c.get("https://httpbin.org/ip")
        print("STATUS:", r.status_code)
        print("BODY:", r.text[:200])
asyncio.run(t())
' 2>&1"""
out, err = ssh_cmd(test_cmd)
print("\n=== PROXY TEST ===")
print(out.strip()[:500])

ssh.close()
