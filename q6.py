import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)

print("=== Worker Logs (last 100 lines) ===")
_, o, e = client.exec_command("docker logs regent-worker --tail 100 2>&1", timeout=30)
print(o.read().decode())

client.close()
