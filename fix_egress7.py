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

# Step 1: Read host config
sftp = ssh.open_sftp()
with sftp.file("/opt/regent/egress/squid.conf", "r") as f:
    original = f.read().decode('utf-8')
sftp.close()

# Step 2: Replace the ACL line
new_config = original.replace(
    "http_access allow localnet evidence_domains",
    "http_access allow localnet"
)
print("Old line found:", "http_access allow localnet evidence_domains" in original)

# Step 3: Write back to host
sftp = ssh.open_sftp()
with sftp.file("/opt/regent/egress/squid.conf", "w") as f:
    f.write(new_config)
sftp.close()
print("Config written to host")

# Step 4: Verify host file
out, err = ssh_cmd("grep -n 'http_access allow' /opt/regent/egress/squid.conf")
print("=== HOST ACL ===")
print(out)

# Step 5: Restart container to pick up new config
out, err = ssh_cmd("docker restart regent-egress && echo 'RESTARTED'")
print("Restart:", out.strip())

time.sleep(3)

# Step 6: Verify container sees new config
out, err = ssh_cmd("docker exec regent-egress grep -n 'http_access allow' /etc/squid/squid.conf")
print("=== CONTAINER ACL ===")
print(out)

# Step 7: Verify squid running
out, err = ssh_cmd("docker exec regent-egress ps aux | grep squid | grep -v grep")
print("Squid running:", "YES" if "squid" in out else out[:200])

# Step 8: Test proxy
out, err = ssh_cmd("""docker exec regent-worker python3 << 'PYEOF'
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=15) as c:
        r = await c.get("https://httpbin.org/ip")
        print("OK", r.status_code, r.text[:100])
asyncio.run(t())
PYEOF""")
print("\n=== PROXY TEST (httpbin) ===")
print(out.strip()[:400])

# Step 9: Test with a domain that was previously failing
out, err = ssh_cmd("""docker exec regent-worker python3 << 'PYEOF'
import httpx, asyncio
async def t():
    urls = ["https://www.theverge.com/rss/index.xml", "https://hnrss.org/frontpage", "https://techcrunch.com/feed/"]
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=15) as c:
        for url in urls:
            try:
                r = await c.get(url)
                print(f"OK {r.status_code} {url[:50]}")
            except Exception as e:
                print(f"FAIL {type(e).__name__} {url[:50]}")
asyncio.run(t())
PYEOF""")
print("\n=== PROXY TEST (evidence domains) ===")
print(out.strip()[:600])

ssh.close()
