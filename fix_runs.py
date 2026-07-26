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

# Step 1: Mark all leaked RUNNING runs as FAILED
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
UPDATE runs
SET status = 'FAILED',
    finished_at = NOW(),
    result = '{\"error\": \"run leaked - created but never started\", \"resolution\": \"auto-cleaned 2026-07-26\"}'::jsonb
WHERE status = 'RUNNING' AND started_at IS NULL;
" """
out, err = ssh_cmd(db_cmd)
print("=== UPDATE RESULT ===")
print(out, err[:200])

# Step 2: Verify
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(db_cmd)
print("=== RUN STATUSES AFTER ===")
print(out)

# Step 3: Check if any RUNNING left
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT COUNT(*) as remaining_running FROM runs WHERE status='RUNNING';
" """
out, err = ssh_cmd(db_cmd)
print("=== REMAINING RUNNING ===")
print(out)

# Step 4: Check works status distribution (should be unchanged, works are independent)
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM works GROUP BY status ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(db_cmd)
print("=== WORK STATUSES ===")
print(out)

# Step 5: Check associated works that might need status cleanup
# Works that had RUNNING runs that are now FAILED
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
WITH affected_works AS (
    SELECT DISTINCT w.id, w.status
    FROM works w
    JOIN runs r ON r.work_id = w.id
    WHERE r.result->>'resolution' = 'auto-cleaned 2026-07-26'
)
SELECT status, COUNT(*) FROM affected_works GROUP BY status;
" """
out, err = ssh_cmd(db_cmd)
print("=== AFFECTED WORKS ===")
print(out)

# Step 6: Check if there are outbox events for the leaked runs
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*)
FROM outbox_events
WHERE payload->>'run_id' IN (
    SELECT id::text FROM runs WHERE result->>'resolution' = 'auto-cleaned 2026-07-26'
)
GROUP BY status;
" """
out, err = ssh_cmd(db_cmd)
print("=== OUTBOX FOR LEAKED RUNS ===")
print(out)

ssh.close()
