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

# Dead letter details
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT e.id, e.event_type, e.status,
       COALESCE(e.last_error, '') as error_msg,
       e.payload->>'goal_id' as goal_id,
       g.status as goal_status
FROM outbox_events e
LEFT JOIN goals g ON (e.payload->>'goal_id')::uuid = g.id
WHERE e.status='DEAD_LETTER'
ORDER BY e.event_type;
" """)
print("=== DEAD LETTERS ===")
print(out)

# Check if any are stale (>24h)
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COUNT(*) FROM outbox_events
WHERE status='DEAD_LETTER' AND occurred_at < NOW() - INTERVAL '24 hours';
" """)
print("=== STALE (>24h) ===")
print(out)

ssh.close()
