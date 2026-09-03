import paramiko, sys

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('118.31.171.159', username='root', password='080900.UI', timeout=10)

cmds = [
    'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"',
    'docker exec regent-api python -c "import regent.novel; print(\'novel module OK\')" 2>&1',
    'docker exec regent-api ls /app/core/src/regent/novel/ 2>&1',
    'docker exec regent-api ls /app/core/migrations/versions/20260903* 2>&1',
    'curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>&1',
    'curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/ 2>&1',
]

for cmd in cmds:
    print(f"\n=== {cmd[:80]} ===")
    stdin, stdout, stderr = c.exec_command(cmd, timeout=15)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out.strip():
        print(out.strip())
    if err.strip():
        print(f"STDERR: {err.strip()}")

c.close()
