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

# Step 1: Write new config to server via sftp
out, err = ssh_cmd("docker exec regent-egress cat /etc/squid/squid.conf")
new_config = out.replace(
    "http_access allow localnet evidence_domains",
    "http_access allow localnet"
)
sftp = ssh.open_sftp()
with sftp.file("/tmp/squid_new.conf", "w") as f:
    f.write(new_config)
sftp.close()

# Step 2: Check volume mounts
out, err = ssh_cmd("docker inspect regent-egress --format '{{json .Mounts}}' 2>&1")
print("=== MOUNTS ===")
print(out[:500])

# Step 3: Check how regent-egress is started
out, err = ssh_cmd("docker inspect regent-egress --format '{{.Config.Cmd}} {{.Config.Entrypoint}}' 2>&1")
print("\n=== ENTRYPOINT/CMD ===")
print(out[:300])

# Step 4: Stop container
out, err = ssh_cmd("docker stop regent-egress && echo 'STOPPED'")
print("\nStop:", out.strip())

# Step 5: Copy config
out, err = ssh_cmd("docker cp /tmp/squid_new.conf regent-egress:/etc/squid/squid.conf 2>&1; echo 'EXIT:'$?")
print("Copy:", out.strip())

# Step 6: Start container
out, err = ssh_cmd("docker start regent-egress && echo 'STARTED'")
print("Start:", out.strip())

time.sleep(3)

# Step 7: Verify
out, err = ssh_cmd("docker exec regent-egress grep -n 'http_access allow' /etc/squid/squid.conf")
print("\n=== NEW ACL ===")
print(out)

out, err = ssh_cmd("docker exec regent-egress ps aux | grep squid | grep -v grep")
print("Squid running:", "YES" if "squid" in out else "NO - " + out[:200])

# Step 8: Test proxy
out, err = ssh_cmd("""docker exec regent-worker python3 << 'PYEOF'
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=15) as c:
        r = await c.get("https://httpbin.org/ip")
        print("OK", r.status_code, r.text[:100])
asyncio.run(t())
PYEOF""")
print("\n=== PROXY TEST ===")
print(out.strip()[:400])

# Step 9: Check that evidence fetching works now
time.sleep(5)
out, err = ssh_cmd("docker logs regent-worker --since 30s 2>&1 | grep -i 'proxyerror\\|403\\|evidence fetch' | tail -5")
print("\n=== RECENT PROXY ERRORS ===")
print(out.strip()[:300] if out.strip() else "(none - good!)")

ssh.close()
