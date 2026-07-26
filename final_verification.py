import paramiko, time, json

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def run(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

print("=" * 60)
print("REGENT SYSTEM FINAL VERIFICATION")
print("=" * 60)

# 1. Docker status
out, err = run("docker ps --filter name=regent --format '{{.Names}} {{.Status}} {{.Image}}'")
print("\n1. DOCKER STATUS")
print(out.strip())

# 2. Health endpoint
out, err = run("""docker exec regent-api python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get("http://localhost:8000/v1/health")
        import json
        d = r.json()
        print(json.dumps(d, indent=2))
asyncio.run(t())
' 2>&1""")
print("\n2. HEALTH ENDPOINT")
print(out.strip()[:800])

# 3. Goal status summary
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) as cnt FROM goals GROUP BY status ORDER BY cnt DESC;
" """)
print("\n3. GOAL STATUS")
print(out.strip())

# 4. Active goal stages
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COALESCE(metadata->>'execution_stage', 'NULL') as stage, COUNT(*)
FROM goals WHERE status='ACTIVE' GROUP BY stage ORDER BY COUNT(*) DESC;
" """)
print("\n4. ACTIVE GOAL STAGES")
print(out.strip())

# 5. Run status
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY COUNT(*) DESC;
" """)
print("\n5. RUN STATUS")
print(out.strip())

# 6. Outbox status
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM outbox_events GROUP BY status ORDER BY COUNT(*) DESC;
" """)
print("\n6. OUTBOX STATUS")
print(out.strip())

# 7. Worker recent activity
out, err = run("docker logs regent-worker --since 60s 2>&1 | tail -15")
print("\n7. WORKER RECENT ACTIVITY")
print(out.strip()[:800])

# 8. Egress proxy test
out, err = run("""docker exec regent-worker python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=10) as c:
        r = await c.get("https://httpbin.org/ip")
        print(f"Egress test: {r.status_code} - {r.text.strip()}")
asyncio.run(t())
' 2>&1""")
print("\n8. EGRESS PROXY TEST")
print(out.strip()[:200])

# 9. Evidence fetch test (real evidence domain)
out, err = run("""docker exec regent-worker python3 -c '
import httpx, asyncio
async def t():
    urls = ["https://hnrss.org/frontpage", "https://www.theverge.com/rss/index.xml"]
    async with httpx.AsyncClient(proxy="http://regent-egress:3128", timeout=10) as c:
        for url in urls:
            r = await c.get(url)
            print(f"Evidence fetch: {r.status_code} - {len(r.content)} bytes - {url[:40]}")
asyncio.run(t())
' 2>&1""")
print("\n9. EVIDENCE FETCH TEST")
print(out.strip()[:300])

# 10. No proxy errors in recent logs
out, err = run("docker logs regent-worker --since 5m 2>&1 | grep -c 'ProxyError\\|403 Forbidden'")
print("\n10. PROXY ERRORS (last 5min)")
print(f"Count: {out.strip()}")

# 11. Reconcile log
out, err = run("cat /var/log/regent-reconcile.log 2>&1")
print("\n11. RECONCILE LOG")
print(out.strip()[:300])

# 12. Cron status
out, err = run("crontab -l 2>&1")
print("\n12. CRON STATUS")
print(out.strip())

ssh.close()
