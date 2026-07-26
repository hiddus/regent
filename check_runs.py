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

# Check runs schema
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name='runs'
ORDER BY ordinal_position;
" """
out, err = ssh_cmd(db_cmd)
print("=== RUNS SCHEMA ===")
print(out)

# Check works schema (for work_status)
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name='works'
ORDER BY ordinal_position;
" """
out, err = ssh_cmd(db_cmd)
print("=== WORKS SCHEMA ===")
print(out)

# Check current run statuses
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(db_cmd)
print("=== RUN STATUSES ===")
print(out)

# Check RUNNING runs detail - do they have lease?
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE lease_expires_at IS NOT NULL) as with_lease,
    COUNT(*) FILTER (WHERE lease_expires_at < NOW()) as lease_expired,
    COUNT(*) FILTER (WHERE started_at < NOW() - INTERVAL '1 hour') as over_1h
FROM runs
WHERE status='RUNNING';
" """
out, err = ssh_cmd(db_cmd)
print("=== RUNNING RUNS DETAIL ===")
print(out)

# Sample of RUNNING runs
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT r.id, r.status, r.work_id, r.lease_expires_at::text, r.started_at::text,
       w.status as work_status, w.goal_id
FROM runs r
JOIN works w ON r.work_id = w.id
WHERE r.status='RUNNING'
LIMIT 10;
" """
out, err = ssh_cmd(db_cmd)
print("=== RUNNING RUNS SAMPLE ===")
print(out)

ssh.close()
