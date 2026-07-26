import paramiko
import json

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

def ssh_cmd(ssh, cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

# 1. Docker status
out, err = ssh_cmd(ssh, "docker ps --format '{{.Names}} {{.Status}} {{.Image}}'")
print("=== DOCKER STATUS ===")
print(out)

# 2. DB comprehensive check
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE status='ACTIVE') as active,
    COUNT(*) FILTER (WHERE status='ACHIEVED') as achieved,
    COUNT(*) FILTER (WHERE status='EXHAUSTED') as exhausted,
    COUNT(*) FILTER (WHERE status='FAILED') as failed,
    COUNT(*) FILTER (WHERE status='DRAFT') as draft,
    COUNT(*) FILTER (WHERE status='READY') as ready,
    COUNT(*) FILTER (WHERE metadata->>'execution_stage' IS NULL AND status='ACTIVE') as null_stage_active
FROM goals;
" """
out, err = ssh_cmd(ssh, db_cmd)
print("\n=== GOAL STATUS ===")
print(out)

# 3. Dead letters
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE payload->>'type' LIKE '%UniqueViolation%' OR error_message LIKE '%unique constraint%') as unique_violation,
    COUNT(*) FILTER (WHERE error_message LIKE '%no metric definitions%') as no_metrics,
    COUNT(*) FILTER (WHERE error_message LIKE '%403%' OR error_message LIKE '%egress%') as egress_403,
    COUNT(*) FILTER (WHERE last_error_time < NOW() - INTERVAL '24 hours') as stale_24h
FROM outbox_events
WHERE status='DEAD_LETTER';
" """
out, err = ssh_cmd(ssh, db_cmd)
print("\n=== DEAD LETTERS ===")
print(out)

# 4. RUNNING runs
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT
    COUNT(*) as running_runs,
    COUNT(*) FILTER (WHERE started_at < NOW() - INTERVAL '1 hour') as over_1h,
    COUNT(*) FILTER (WHERE started_at < NOW() - INTERVAL '6 hours') as over_6h,
    COUNT(*) FILTER (WHERE started_at < NOW() - INTERVAL '24 hours') as over_24h
FROM runs
WHERE status='RUNNING';
" """
out, err = ssh_cmd(ssh, db_cmd)
print("\n=== RUNNING RUNS ===")
print(out)

# 5. Egress proxy config
out, err = ssh_cmd(ssh, "docker exec regent-egress cat /app/.env 2>/dev/null || echo 'NO_ENV'; docker exec regent-egress env 2>/dev/null | grep -i proxy || echo 'NO_PROXY_ENV'")
print("\n=== EGRESS CONFIG ===")
print(out[:500])

# 6. Worker logs - last errors
out, err = ssh_cmd(ssh, "docker logs regent-worker --tail 100 2>&1 | grep -i -E 'error|exception|traceback|403|timeout|failed' | tail -20")
print("\n=== WORKER RECENT ERRORS ===")
print(out)

# 7. Outbox events summary
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT status, COUNT(*) FROM outbox_events GROUP BY status ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(ssh, db_cmd)
print("\n=== OUTBOX EVENTS ===")
print(out)

# 8. Stage distribution for ACTIVE goals
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT COALESCE(metadata->>'execution_stage', 'NULL') as stage, COUNT(*)
FROM goals
WHERE status='ACTIVE'
GROUP BY stage
ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(ssh, db_cmd)
print("\n=== ACTIVE GOALS BY STAGE ===")
print(out)

ssh.close()
