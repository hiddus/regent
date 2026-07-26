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

# Check existing PreviewDeploymentSucceeded event payload format
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT payload::text
FROM outbox_events
WHERE payload->>'type' = 'PreviewDeploymentSucceeded'
LIMIT 2;
" """
out, err = ssh_cmd(sql)
print("=== PREVIEW DEPLOYMENT SUCCEEDED EVENT FORMAT ===")
print(out[:1500])

# Also check GoalStateChanged format (these are created after gate evaluation)
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT payload::text
FROM outbox_events
WHERE payload->>'type' = 'GoalStateChanged'
LIMIT 2;
" """
out, err = ssh_cmd(sql)
print("\n=== GOAL STATE CHANGED EVENT FORMAT ===")
print(out[:1000])

ssh.close()
