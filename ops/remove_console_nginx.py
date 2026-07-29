"""Remove the standalone regent-console nginx container on port 3000."""
import paramiko
from dotenv import dotenv_values
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = dotenv_values(ROOT / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

_, o, e = ssh.exec_command("docker rm -f regent-console 2>&1")
print(o.read().decode().strip())
ssh.close()
print("REMOVED")
