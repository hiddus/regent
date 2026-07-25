"""Check Squid proxy config and test specific URLs."""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("118.31.171.159", username="root", password="080900.UI", timeout=15)

def run(name, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"\n=== {name} ===")
    if out:
        print(out[-3000:] if len(out) > 3000 else out)
    if err and "WARNING" not in err and "DEPRECATED" not in err:
        print(f"STDERR: {err[-500:]}")

# 1. Check Squid config
run("Squid Config", "cat /opt/regent/egress/squid.conf")

# 2. Test specific URLs through proxy
run("Test TechCrunch",
    "docker exec regent-worker python -c \"import httpx; r=httpx.get('https://techcrunch.com/feed/', proxy='http://regent-egress:3128', timeout=10, follow_redirects=True); print(r.status_code, len(r.text))\"")

run("Test 36kr",
    "docker exec regent-worker python -c \"import httpx; r=httpx.get('https://www.36kr.com/feed', proxy='http://regent-egress:3128', timeout=10, follow_redirects=True); print(r.status_code, len(r.text))\"")

run("Test HackerNews",
    "docker exec regent-worker python -c \"import httpx; r=httpx.get('https://hnrss.org/frontpage', proxy='http://regent-egress:3128', timeout=10, follow_redirects=True); print(r.status_code, len(r.text))\"")

# 3. Check Squid logs
run("Squid Logs", "docker logs regent-egress --since 10m 2>&1 | tail -30")

client.close()
