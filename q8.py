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

GOAL_ID = "62a41a1c-5486-4295-a415-6e728caca5bc"

print("=== Goal Status ===")
out = run_cmd(PSQL + f' -c "SELECT id, status FROM goals WHERE id=\'{GOAL_ID}\'"')
print(out if out else "(empty)")

print("\n=== Goal Metadata ===")
out = run_cmd(PSQL + f' -c "SELECT metadata_json::text FROM goals WHERE id=\'{GOAL_ID}\'"')
print(out[:2000] if out else "(empty)")

print("\n=== Latest Deployments ===")
out = run_cmd(PSQL + ' -c "SELECT id, status, endpoint, correlation_id FROM deployments ORDER BY created_at DESC LIMIT 5"')
print(out if out else "(empty)")

print("\n=== Preview URL Test ===")
out = run_cmd('curl -s -o /dev/null -w "%{http_code}" http://118.31.171.159:8000/preview/efde5d0d-a734-44ec-a013-bcd8c3cb2495/b0da6b9a-cf6c-4c98-8fc3-5fa69c61a8fd/')
print(f"HTTP Status: {out}")

client.close()
