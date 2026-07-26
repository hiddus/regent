import paramiko

hostname = '118.31.171.159'
username = 'root'
password = '080900.UI'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(hostname, username=username, password=password, port=22)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    return stdout.read().decode('utf-8', errors='ignore'), stderr.read().decode('utf-8', errors='ignore')

queries = [
    ("Goals columns",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='goals' ORDER BY ordinal_position;\""),
    ("Outbox columns",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='outbox_events' ORDER BY ordinal_position;\""),
    ("Works columns",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='works' ORDER BY ordinal_position;\""),
    ("Organizations columns",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='organizations' ORDER BY ordinal_position;\""),
    ("All tables",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;\""),
]

for label, cmd in queries:
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    out, err = run(cmd)
    if out.strip():
        print(out[:2000])
    if err.strip():
        print(f"STDERR: {err[:500]}")

ssh.close()
