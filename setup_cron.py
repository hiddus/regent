import paramiko, time

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def run(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

# Create a reconciliation script on the host
script = """#!/bin/bash
# Regent Run-Lease Reconciliation Cron Job
# Runs every 5 minutes to clean leaked runs and report health

LOGFILE="/var/log/regent-reconcile.log"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Clean leaked RUNNING runs (NULL started_at or >1h old)
CLEANED=$(docker exec regent-postgres psql -U regent -d regent -t -A -c "
UPDATE runs SET status='FAILED', finished_at=NOW(),
result = jsonb_build_object('error','run leaked - auto-reconciled','reconciled_at', NOW()::text)
WHERE status='RUNNING' AND (started_at IS NULL OR started_at < NOW() - INTERVAL '1 hour')
RETURNING id;
" 2>/dev/null | wc -l)

# Get health metrics
HEALTH=$(docker exec regent-api python3 -c "
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get('http://localhost:8000/v1/health')
        print(r.text)
asyncio.run(t())
" 2>/dev/null)

# Count dead letters
DL=$(docker exec regent-postgres psql -U regent -d regent -t -A -c "SELECT count(*) FROM outbox_events WHERE status='DEAD_LETTER';" 2>/dev/null)

# Log
echo "[$TIMESTAMP] cleaned_runs=$CLEANED dead_letters=$DL health=$HEALTH" >> $LOGFILE
"""

sftp = ssh.open_sftp()
with sftp.file("/opt/regent/reconcile.sh", "w") as f:
    f.write(script)
sftp.close()

# Make executable
out, err = run("chmod +x /opt/regent/reconcile.sh && echo 'OK'")
print("Make executable:", out.strip())

# Add to crontab (every 5 minutes)
out, err = run("(crontab -l 2>/dev/null | grep -v 'regent-reconcile'; echo '*/5 * * * * /opt/regent/reconcile.sh') | crontab - && echo 'CRON_ADDED'")
print("Cron add:", out.strip())

# Verify crontab
out, err = run("crontab -l 2>&1")
print("Crontab:", out.strip())

# Run once to test
out, err = run("/opt/regent/reconcile.sh 2>&1; echo 'EXIT:'$?")
print("\nTest run:", out.strip()[:300])

# Check log
out, err = run("cat /var/log/regent-reconcile.log 2>&1")
print("\n=== RECONCILE LOG ===")
print(out.strip()[:500])

# Verify health endpoint one more time
out, err = run("""docker exec regent-api python3 -c '
import httpx, asyncio
async def t():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get("http://localhost:8000/v1/health")
        d = r.json()
        print(f"Status: {d[\"status\"]}")
        print(f"Metrics: {d[\"metrics\"]}")
        print(f"Stages: {d[\"active_goal_stages\"]}")
asyncio.run(t())
' 2>&1""")
print("\n=== FINAL HEALTH ===")
print(out.strip()[:500])

ssh.close()
