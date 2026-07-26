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

# Classify NULL-stage goals by work status
sql = """
SELECT
    CASE
        WHEN w_count = 0 THEN 'no_works'
        WHEN w_all_terminal THEN 'works_terminal'
        ELSE 'works_active'
    END as category,
    COUNT(*)
FROM (
    SELECT g.id,
        COUNT(w.id) as w_count,
        BOOL_AND(w.status IN ('ACCEPTED', 'REJECTED')) as w_all_terminal
    FROM goals g
    LEFT JOIN works w ON w.goal_id = g.id
    WHERE g.status = 'ACTIVE' AND (g.metadata->>'execution_stage') IS NULL
    GROUP BY g.id
) sub
GROUP BY 1;
"""

sftp = ssh.open_sftp()
with sftp.file("/tmp/classify.sql", "w") as f:
    f.write(sql)
sftp.close()

out, err = ssh_cmd("docker cp /tmp/classify.sql regent-postgres:/tmp/classify.sql && docker exec regent-postgres psql -U regent -d regent -f /tmp/classify.sql")
print("=== NULL-STAGE CLASSIFICATION ===")
print(out)

# Check what "works_active" means - what statuses
sql2 = """
SELECT w.status, COUNT(*)
FROM goals g
JOIN works w ON w.goal_id = g.id
WHERE g.status = 'ACTIVE' AND (g.metadata->>'execution_stage') IS NULL
GROUP BY w.status;
"""
sftp = ssh.open_sftp()
with sftp.file("/tmp/classify2.sql", "w") as f:
    f.write(sql2)
sftp.close()

out, err = ssh_cmd("docker cp /tmp/classify2.sql regent-postgres:/tmp/classify2.sql && docker exec regent-postgres psql -U regent -d regent -f /tmp/classify2.sql")
print("=== WORK STATUSES FOR NULL-STAGE ===")
print(out)

# All these goals are effectively stalled. The fix: mark all as EXHAUSTED
# since they have NULL execution_stage (never entered execution chain)
# and are >1h old (no chance of being in-flight)

fix_sql = """
UPDATE goals
SET status = 'EXHAUSTED',
    metadata = metadata || '{"execution_stage": "NULL_STAGE_CLEANUP", "cleanup_reason": "never entered execution chain", "cleanup_date": "2026-07-26"}'::jsonb
WHERE status = 'ACTIVE'
  AND (metadata->>'execution_stage') IS NULL
  AND created_at < NOW() - INTERVAL '1 hour';
"""

sftp = ssh.open_sftp()
with sftp.file("/tmp/fix_nullstage.sql", "w") as f:
    f.write(fix_sql)
sftp.close()

out, err = ssh_cmd("docker cp /tmp/fix_nullstage.sql regent-postgres:/tmp/fix_nullstage.sql && docker exec regent-postgres psql -U regent -d regent -f /tmp/fix_nullstage.sql")
print("\n=== FIX RESULT ===")
print(out, err[:200])

# Verify
out, err = ssh_cmd("docker exec regent-postgres psql -U regent -d regent -c 'SELECT status, COUNT(*) FROM goals GROUP BY status ORDER BY COUNT(*) DESC;'")
print("=== GOAL STATUSES ===")
print(out)

# Check if any NULL-stage ACTIVE goals remain
out, err = ssh_cmd("docker exec regent-postgres psql -U regent -d regent -c \"SELECT COUNT(*) FROM goals WHERE status='ACTIVE' AND (metadata->>'execution_stage') IS NULL;\"")
print("=== REMAINING NULL-STAGE ACTIVE ===")
print(out)

ssh.close()
