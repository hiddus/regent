import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)

print("=== Preview URL Test ===")
_, o, e = client.exec_command('curl -s http://118.31.171.159:8000/preview/31081033-3c72-42fe-8463-3e522ed7d1e1/9bdd260f-aa0b-4897-9470-abfd1a7fdfcf/ | head -50', timeout=30)
print(o.read().decode())

print("\n=== Check data-regent-event ===")
_, o, e = client.exec_command('curl -s http://118.31.171.159:8000/preview/31081033-3c72-42fe-8463-3e522ed7d1e1/9bdd260f-aa0b-4897-9470-abfd1a7fdfcf/ | grep -o "data-regent-event[^"]*"', timeout=30)
print(o.read().decode() if o.read else "(not found)")

print("\n=== Check main landmark ===")
_, o, e = client.exec_command('curl -s http://118.31.171.159:8000/preview/31081033-3c72-42fe-8463-3e522ed7d1e1/9bdd260f-aa0b-4897-9470-abfd1a7fdfcf/ | grep -c "<main"', timeout=30)
print(f"<main> tags: {o.read().decode().strip()}")

print("\n=== Check outbound links ===")
_, o, e = client.exec_command('curl -s http://118.31.171.159:8000/preview/31081033-3c72-42fe-8463-3e522ed7d1e1/9bdd260f-aa0b-4897-9470-abfd1a7fdfcf/ | grep -o "https\\?://[^\\"]*" | head -10', timeout=30)
print(o.read().decode() if o.read else "(no links)")

client.close()
