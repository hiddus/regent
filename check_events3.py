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

# Check PreviewDeploymentSucceeded payload format
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id, payload::text
FROM outbox_events
WHERE event_type = 'PreviewDeploymentSucceeded'
LIMIT 5;
" """
out, err = ssh_cmd(sql)
print("=== PREVIEW DEPLOYMENT SUCCEEDED ===")
print(out[:2000])

# Also check GoalExecutionRequested format (for goals without deployment_id)
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id, payload::text
FROM outbox_events
WHERE event_type = 'GoalExecutionRequested'
LIMIT 3;
" """
out, err = ssh_cmd(sql)
print("\n=== GOAL EXECUTION REQUESTED ===")
print(out[:1500])

ssh.close()
