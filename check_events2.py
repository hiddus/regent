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

# Check outbox_events schema
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name='outbox_events'
ORDER BY ordinal_position;
" """
out, err = ssh_cmd(sql)
print("=== OUTBOX SCHEMA ===")
print(out)

# Check all event types and counts
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT event_type, COUNT(*) FROM outbox_events GROUP BY event_type ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(sql)
print("=== EVENT TYPES ===")
print(out)

# Check a sample event payload
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id, event_type, status, payload::text
FROM outbox_events
LIMIT 3;
" """
out, err = ssh_cmd(sql)
print("=== SAMPLE EVENTS ===")
print(out[:2000])

ssh.close()
