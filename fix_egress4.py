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

# Step 1: Get full config
out, err = ssh_cmd("docker exec regent-egress cat /etc/squid/squid.conf")

# Step 2: Replace the line
new_config = out.replace(
    "http_access allow localnet evidence_domains",
    "http_access allow localnet"
)

# Step 3: Write to remote temp file
sftp = ssh.open_sftp()
with sftp.file("/tmp/squid_new.conf", "w") as f:
    f.write(new_config)
sftp.close()
print("Config written to /tmp/squid_new.conf")

# Step 4: docker cp into container
out, err = ssh_cmd("docker cp /tmp/squid_new.conf regent-egress:/etc/squid/squid.conf && echo 'COPIED'")
print("Copy:", out.strip(), err.strip()[:200])

# Step 5: Verify the change
out, err = ssh_cmd("docker exec regent-egress grep -n 'http_access allow' /etc/squid/squid.conf")
print("=== NEW ACL ===")
print(out)

# Step 6: Restart squid inside container (kill and restart since -N mode)
out, err = ssh_cmd("docker exec regent-egress sh -c 'kill $(cat /var/run/squid.pid) 2>/dev/null; sleep 1; squid -f /etc/squid/squid.conf -NYCd 1 &' && echo 'RESTARTED'")
print("Squid restart:", out.strip())

import time
time.sleep(3)

# Step 7: Verify squid is running
out, err = ssh_cmd("docker exec regent-egress ps aux | grep squid | grep -v grep")
print("Squid processes:", out.strip()[:300])

# Step 8: Test proxy
out, err = ssh_cmd("docker exec regent-worker python3 -c 'import httpx,asyncio; exec(\"async def t():\\n async with httpx.AsyncClient(proxy=\\\"http://regent-egress:3128\\\",timeout=10) as c:\\n  r=await c.get(\\\"https://httpbin.org/ip\\\")\\n  print(\\\"OK\\\",r.status_code)\"); asyncio.run(t())' 2>&1")
print("=== PROXY TEST ===")
print(out.strip()[:400])

# Step 9: Test worker evidence fetch
out, err = ssh_cmd("docker logs regent-worker --since 10s 2>&1 | tail -10")
print("\n=== RECENT WORKER LOGS ===")
print(out.strip()[:500])

ssh.close()
