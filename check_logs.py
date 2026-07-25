"""Check delivery gap reasons - full logs."""
import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)

# Get full worker logs related to the latest goal
print("=== Worker Logs (last 200 lines) ===")
_, stdout, _ = client.exec_command("docker logs regent-worker 2>&1 | tail -200", timeout=30)
print(stdout.read().decode().strip())

client.close()
