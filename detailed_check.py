import paramiko
import json

hostname = '118.31.171.159'
username = 'root'
password = '080900.UI'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(hostname, username=username, password=password, port=22)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    return stdout.read().decode('utf-8', errors='ignore'), stderr.read().decode('utf-8', errors='ignore')

# Try simpler queries first
queries = [
    ("Goals sample", 
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, status, name FROM goals ORDER BY updated_at DESC LIMIT 20;\""),
    ("ACHIEVED goals",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, name FROM goals WHERE status='ACHIEVED';\""),
    ("EXHAUSTED goals",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, name FROM goals WHERE status='EXHAUSTED';\""),
    ("Goal 6a286e4e",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, status, name, updated_at FROM goals WHERE id='6a286e4e';\""),
    ("Dead letters detail",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, event_type, error_message FROM outbox_events WHERE status='DEAD_LETTER';\""),
    ("Organizations",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, name, creation_strategy FROM organizations;\""),
    ("Goals with org_id",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT count(*) FROM goals WHERE organization_id IS NOT NULL;\""),
    ("Works for 6a286e4e",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT work_type, status, count(*) FROM works WHERE goal_id='6a286e4e' GROUP BY 1,2;\""),
    ("Worker logs recent",
     "docker logs regent-worker --since 60m 2>&1 | tail -80"),
    ("API home",
     "curl -s http://localhost:8000/ 2>&1 | head -5"),
    ("Goals metadata stage gate_result",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, status, metadata->>'execution_stage' as stage, metadata->>'gate_result' as gate FROM goals WHERE status IN ('ACTIVE','ACHIEVED','EXHAUSTED') ORDER BY updated_at DESC LIMIT 20;\""),
    ("New PENDING events",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT event_type, count(*) FROM outbox_events WHERE status='PENDING' GROUP BY 1;\""),
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
