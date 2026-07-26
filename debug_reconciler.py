import paramiko, time

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def run(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

# Check if reconciler file exists in worker
out, err = run("docker exec regent-worker ls -la /usr/local/lib/python3.12/site-packages/regent/infrastructure/run_reconciler.py 2>&1")
print("File exists:", out.strip())

# Try to run it directly and see error
out, err = run("docker exec regent-worker python3 -c 'from regent.infrastructure.run_reconciler import main; print(\"import OK\")' 2>&1")
print("Import test:", out.strip()[:500], err.strip()[:500])

# Try to run it with timeout to see if it starts
out, err = run("docker exec regent-worker timeout 5 python3 -m regent.infrastructure.run_reconciler 2>&1")
print("\nRun test (5s timeout):", out.strip()[:500], err.strip()[:500])

# Check env vars
out, err = run("docker exec regent-worker env | grep -i 'REGENT\\|DATABASE\\|POSTGRES' 2>&1 | head -20")
print("\n=== ENV ===")
print(out.strip())

ssh.close()
