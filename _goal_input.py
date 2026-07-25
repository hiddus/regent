"""Check goal original_input for failing goals."""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("118.31.171.159", username="root", password="080900.UI", timeout=15)

def run(name, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"\n=== {name} ===")
    if out:
        print(out[-5000:] if len(out) > 5000 else out)
    if err and "WARNING" not in err and "DEPRECATED" not in err:
        print(f"STDERR: {err[-1000:]}")

# 1. Working goal original_input
run("Working Goal Input",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, original_input FROM goals WHERE id='7aeb0c18-52a7-410b-83dd-8678c67ed260'\"")

# 2. Failing goal original_input
run("Failing Goal Input",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, original_input FROM goals WHERE id='c197690d-d80c-4589-80bb-c2ecb67fd4e7'\"")

# 3. All goals original_input
run("All Goals Input",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, original_input FROM goals ORDER BY created_at DESC LIMIT 5\"")

client.close()
