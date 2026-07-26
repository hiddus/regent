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

# Step 1: Backup current config
out, err = ssh_cmd("docker exec regent-egress cp /etc/squid/squid.conf /etc/squid/squid.conf.bak && echo 'BACKUP_OK'")
print("Backup:", out.strip())

# Step 2: Read current config to confirm the line we want to change
out, err = ssh_cmd("docker exec regent-egress cat /etc/squid/squid.conf | grep -n 'http_access allow localnet'")
print("Current ACL line:", out.strip())

# Step 3: Modify — replace "allow localnet evidence_domains" with "allow localnet"
out, err = ssh_cmd("docker exec regent-egress sed -i 's/http_access allow localnet evidence_domains/http_access allow localnet/' /etc/squid/squid.conf && echo 'MODIFY_OK'")
print("Modify:", out.strip())

# Step 4: Verify the change
out, err = ssh_cmd("docker exec regent-egress cat /etc/squid/squid.conf | grep -n 'http_access allow localnet'")
print("New ACL line:", out.strip())

# Step 5: Check config syntax
out, err = ssh_cmd("docker exec regent-egress squid -k parse 2>&1 | tail -5")
print("Config parse:", out.strip())

# Step 6: Reload Squid
out, err = ssh_cmd("docker exec regent-egress squid -k reconfigure 2>&1; echo 'RELOAD_OK'")
print("Reload:", out.strip())

# Step 7: Test proxy from worker
test_cmd = """docker exec regent-worker python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=10) as c:
        r = await c.get("https://httpbin.org/ip")
        print("STATUS:", r.status_code, r.text[:200])
asyncio.run(t())
' 2>&1"""
out, err = ssh_cmd(test_cmd)
print("Proxy test:", out.strip()[:400])
if err:
    print("Proxy test err:", err.strip()[:200])

# Step 8: Check if evidence fetch now works by triggering a discovery
out, err = ssh_cmd("docker logs regent-worker --tail 5 --since 30s 2>&1")
print("\nRecent worker logs:", out.strip()[:500])

ssh.close()
