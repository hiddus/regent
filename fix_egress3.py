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
original = out

# Step 2: Replace the line
new_config = original.replace(
    "http_access allow localnet evidence_domains",
    "http_access allow localnet"
)
print("=== CHECK REPLACEMENT ===")
if "http_access allow localnet evidence_domains" not in new_config:
    print("Old line removed - OK")
if "http_access allow localnet" in new_config:
    print("New line present - OK")

# Step 3: Write new config to container
# Use base64 to avoid shell escaping issues
import base64
encoded = base64.b64encode(new_config.encode()).decode()

cmd = f"echo '{encoded}' | base64 -d | docker exec -i regent-egress tee /etc/squid/squid.conf > /dev/null && echo 'WRITTEN'"
out, err = ssh_cmd(cmd)
print("Write result:", out.strip(), err.strip()[:200])

# Step 4: Verify
out, err = ssh_cmd("docker exec regent-egress grep -n 'http_access allow' /etc/squid/squid.conf")
print("=== VERIFY ACL ===")
print(out)

# Step 5: Restart squid inside container
out, err = ssh_cmd("docker exec regent-egress squid -k reconfigure 2>&1; echo 'EXIT:'$?")
print("Reconfigure:", out.strip())

# If reconfigure doesn't work (squid in -N mode), restart container
out, err = ssh_cmd("docker restart regent-egress && echo 'RESTARTED'")
print("Container restart:", out.strip())

import time
time.sleep(3)

# Step 6: Verify Squid is running
out, err = ssh_cmd("docker ps --filter name=regent-egress --format '{{.Status}}'")
print("Egress status:", out.strip())

# Step 7: Test proxy
out, err = ssh_cmd("docker exec regent-worker python3 -c 'import httpx,asyncio; exec(\"async def t():\\n async with httpx.AsyncClient(proxy=\\\"http://regent-egress:3128\\\",timeout=10) as c:\\n  r=await c.get(\\\"https://httpbin.org/ip\\\")\\n  print(\\\"OK\\\",r.status_code)\"); asyncio.run(t())' 2>&1")
print("=== PROXY TEST ===")
print(out.strip()[:400])

ssh.close()
