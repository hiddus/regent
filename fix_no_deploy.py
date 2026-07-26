import paramiko

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def run(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

# Mark remaining 5 PREVIEW_SUCCEEDED without deployment_id as EXHAUSTED
sql = """
UPDATE goals
SET status = 'EXHAUSTED',
    metadata = metadata || '{"execution_stage": "PREVIEW_SUCCEEDED_NO_DEPLOY", "cleanup_reason": "stuck at PREVIEW_SUCCEEDED with no deployment_id to re-trigger", "cleanup_date": "2026-07-26"}'::jsonb
WHERE status = 'ACTIVE'
  AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
  AND (metadata->>'last_deployment_id' IS NULL OR metadata->>'last_deployment_id' = '');
"""

sftp = ssh.open_sftp()
with sftp.file("/tmp/fix_no_deploy.sql", "w") as f:
    f.write(sql)
sftp.close()

out, err = run("docker cp /tmp/fix_no_deploy.sql regent-postgres:/tmp/fix_no_deploy.sql && docker exec regent-postgres psql -U regent -d regent -f /tmp/fix_no_deploy.sql")
print("Fix result:", out.strip(), err.strip()[:200])

# Verify
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM goals GROUP BY status ORDER BY COUNT(*) DESC;
" """)
print("\n=== GOAL STATUSES ===")
print(out)

# Active stages
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COALESCE(metadata->>'execution_stage', 'NULL') as stage, COUNT(*)
FROM goals WHERE status='ACTIVE' GROUP BY stage ORDER BY COUNT(*) DESC;
" """)
print("=== ACTIVE STAGES ===")
print(out)

# Count PREVIEW_SUCCEEDED
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COUNT(*) FROM goals WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED';
" """)
print("=== PREVIEW_SUCCEEDED REMAINING ===")
print(out)

ssh.close()
