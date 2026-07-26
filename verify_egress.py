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

# Write test script to server
sftp = ssh.open_sftp()
with sftp.file("/tmp/proxy_test.py", "w") as f:
    f.write("""import httpx, asyncio

async def test_url(url, name):
    try:
        async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=15) as c:
            r = await c.get(url)
            print(f"OK {name}: {r.status_code} ({len(r.content)} bytes)")
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__}: {e}")

async def main():
    await test_url("https://httpbin.org/ip", "httpbin")
    await test_url("https://www.theverge.com/rss/index.xml", "theverge")
    await test_url("https://hnrss.org/frontpage", "hnrss")
    await test_url("https://techcrunch.com/feed/", "techcrunch")

asyncio.run(main())
""")
sftp.close()

# Copy to worker and run
out, err = ssh_cmd("docker cp /tmp/proxy_test.py regent-worker:/tmp/proxy_test.py && echo 'COPIED'")
print("Copy:", out.strip())

out, err = ssh_cmd("docker exec regent-worker python3 /tmp/proxy_test.py 2>&1")
print("\n=== PROXY TEST ===")
print(out.strip())

# Also check Squid access log for requests
out, err = ssh_cmd("docker exec regent-egress tail -20 /var/log/squid/access.log 2>&1")
print("\n=== SQUID ACCESS LOG ===")
print(out.strip()[-500:])

# Check worker evidence fetch errors since restart
out, err = ssh_cmd("docker logs regent-worker --since 1m 2>&1 | grep -i 'proxyerror\\|403 forbidden\\|evidence fetch failed' | tail -5")
print("\n=== WORKER PROXY ERRORS (last 1m) ===")
print(out.strip() if out.strip() else "(none - egress fixed!)")

ssh.close()
