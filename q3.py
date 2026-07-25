import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

def run_cmd(cmd):
    _, o, e = client.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

print("=== Deployment Columns ===")
out = run_cmd(PSQL + ' -c "SELECT column_name FROM information_schema.columns WHERE table_name=\'deployments\' ORDER BY ordinal_position"')
print(out)

print("\n=== Recent Deployments ===")
out = run_cmd(PSQL + ' -c "SELECT * FROM deployments ORDER BY created_at DESC LIMIT 3"')
print(out if out else "(empty)")

print("\n=== Deployment columns with types ===")
out = run_cmd(PSQL + ' -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name=\'deployments\' ORDER BY ordinal_position"')
print(out)

client.close()
