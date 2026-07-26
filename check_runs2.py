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

# Check run age distribution
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    CASE
        WHEN started_at > NOW() - INTERVAL '10 minutes' THEN '0-10min'
        WHEN started_at > NOW() - INTERVAL '1 hour' THEN '10min-1h'
        WHEN started_at > NOW() - INTERVAL '6 hours' THEN '1h-6h'
        WHEN started_at > NOW() - INTERVAL '24 hours' THEN '6h-24h'
        ELSE '>24h'
    END as age,
    COUNT(*)
FROM runs
WHERE status='RUNNING'
GROUP BY age
ORDER BY MIN(started_at);
" """
out, err = ssh_cmd(db_cmd)
print("=== RUN AGE DISTRIBUTION ===")
print(out)

# Check works status for RUNNING runs
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT w.status as work_status, COUNT(*)
FROM runs r
JOIN works w ON r.work_id = w.id
WHERE r.status='RUNNING'
GROUP BY w.status;
" """
out, err = ssh_cmd(db_cmd)
print("=== WORK STATUS FOR RUNNING RUNS ===")
print(out)

# Check goal status for these runs
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT g.status as goal_status, COUNT(DISTINCT g.id) as goals
FROM runs r
JOIN works w ON r.work_id = w.id
JOIN goals g ON w.goal_id = g.id
WHERE r.status='RUNNING'
GROUP BY g.status;
" """
out, err = ssh_cmd(db_cmd)
print("=== GOAL STATUS FOR RUNNING RUNS ===")
print(out)

# Check if any RUNNING runs have started_at NULL
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT COUNT(*) FROM runs WHERE status='RUNNING' AND started_at IS NULL;
" """
out, err = ssh_cmd(db_cmd)
print("=== NULL STARTED_AT ===")
print(out)

# Sample RUNNING runs with work and goal
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT r.id, r.status as run_status, r.started_at::text,
       w.id as work_id, w.status as work_status,
       g.id as goal_id, g.status as goal_status
FROM runs r
JOIN works w ON r.work_id = w.id
JOIN goals g ON w.goal_id = g.id
WHERE r.status='RUNNING'
ORDER BY r.started_at DESC NULLS LAST
LIMIT 15;
" """
out, err = ssh_cmd(db_cmd)
print("=== RUNNING RUNS SAMPLE ===")
print(out)

ssh.close()
