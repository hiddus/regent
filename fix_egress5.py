import paramiko, time

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def ssh_cmd(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

# Step 1: Get current config, modify, write to /tmp/squid_new.conf on server
out, err = ssh_cmd("docker exec regent-egress cat /etc/squid/squid.conf")
new_config = out.replace(
    "http_access allow localnet evidence_domains",
    "http_access allow localnet"
)

# Write via sftp
sftp = ssh.open_sftp()
with sftp.file("/tmp/squid_new.conf", "w") as f:
    f.write(new_config)
sftp.close()
print("Config written to /tmp/squid_new.conf")

# Step 2: Kill squid inside container, replace config, restart
fix_cmd = """docker exec regent-egress sh -c '
kill $(cat /var/run/squid.pid) 2>/dev/null
sleep 2
' && echo 'SQUID_KILLED'"""

out, err = ssh_cmd(fix_cmd)
print("Kill squid:", out.strip())

time.sleep(2)

# Step 3: Copy new config
out, err = ssh_cmd("docker cp /tmp/squid_new.conf regent-egress:/etc/squid/squid.conf && echo 'COPIED_OK'")
print("Copy config:", out.strip(), err.strip()[:200])

# Step 4: Start squid
out, err = ssh_cmd("docker exec -d regent-egress squid -f /etc/squid/squid.conf -NYCd 1 && echo 'STARTED'")
print("Start squid:", out.strip(), err.strip()[:200])

time.sleep(2)

# Step 5: Verify
out, err = ssh_cmd("docker exec regent-egress grep -n 'http_access allow' /etc/squid/squid.conf")
print("=== NEW ACL ===")
print(out)

out, err = ssh_cmd("docker exec regent-egress ps aux | grep squid | grep -v grep")
print("Squid process:", out.strip()[:300])

# Step 6: Test proxy
test_cmd = """docker exec regent-worker python3 << 'PYEOF'
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=10) as c:
        r = await c.get("https://httpbin.org/ip")
        print("OK", r.status_code, r.text[:100])
asyncio.run(t())
PYEOF"""
out, err = ssh_cmd(test_cmd)
print("\n=== PROXY TEST ===")
print(out.strip()[:400])

# Step 7: Check for new evidence fetch errors
out, err = ssh_cmd("docker logs regent-worker --since 20s 2>&1 | grep -i 'proxyerror\|403\|evidence fetch' | tail -5")
print("\n=== RECENT PROXY ERRORS ===")
print(out.strip()[:300])

ssh.close()
