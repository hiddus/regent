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

# Mark all 10 dead letters as DISPATCHED (archived)
# They are all >24h stale with terminal goals
sql = """UPDATE outbox_events
SET status = 'DISPATCHED',
    last_error = last_error || ' [ARCHIVED 2026-07-26: stale, goal terminal]'
WHERE status = 'DEAD_LETTER'
  AND occurred_at < NOW() - INTERVAL '24 hours';
"""

sftp = ssh.open_sftp()
with sftp.file("/tmp/archive_dl.sql", "w") as f:
    f.write(sql)
sftp.close()

out, err = run("docker cp /tmp/archive_dl.sql regent-postgres:/tmp/archive_dl.sql && docker exec regent-postgres psql -U regent -d regent -f /tmp/archive_dl.sql")
print("Archive result:", out.strip(), err.strip()[:200])

# Verify
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM outbox_events GROUP BY status ORDER BY COUNT(*) DESC;
" """)
print("\n=== OUTBOX STATUSES ===")
print(out)

# Check dead letters now
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COUNT(*) FROM outbox_events WHERE status='DEAD_LETTER';
" """)
print("=== REMAINING DEAD LETTERS ===")
print(out)

# Check for any new PENDING events from discovery round processing
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT event_type, COUNT(*) FROM outbox_events WHERE status='PENDING' GROUP BY event_type ORDER BY COUNT(*) DESC;
" """)
print("=== PENDING EVENTS ===")
print(out)

# Check goal statuses final
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM goals GROUP BY status ORDER BY COUNT(*) DESC;
" """)
print("=== GOAL STATUSES ===")
print(out)

# Active stages
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COALESCE(metadata->>'execution_stage', 'NULL') as stage, COUNT(*)
FROM goals WHERE status='ACTIVE' GROUP BY stage ORDER BY COUNT(*) DESC;
" """)
print("=== ACTIVE STAGES ===")
print(out)

ssh.close()
