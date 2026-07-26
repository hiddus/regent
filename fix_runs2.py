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

# Write SQL to server
sftp = ssh.open_sftp()
with sftp.file("/tmp/fix_runs.sql", "w") as f:
    f.write("""UPDATE runs
SET status = 'FAILED',
    finished_at = NOW(),
    result = '{"error": "run leaked - never started", "resolution": "auto-cleaned"}'::jsonb
WHERE status = 'RUNNING' AND started_at IS NULL;
""")
sftp.close()

# Copy to postgres container and execute
out, err = ssh_cmd("docker cp /tmp/fix_runs.sql regent-postgres:/tmp/fix_runs.sql && echo 'COPIED'")
print("Copy:", out.strip())

out, err = ssh_cmd("docker exec regent-postgres psql -U regent -d regent -f /tmp/fix_runs.sql 2>&1")
print("SQL result:", out.strip(), err.strip()[:200])

# Verify
out, err = ssh_cmd("docker exec regent-postgres psql -U regent -d regent -c 'SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY COUNT(*) DESC;' 2>&1")
print("\n=== RUN STATUSES ===")
print(out.strip())

ssh.close()
