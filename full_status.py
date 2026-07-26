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
    ("Goals summary",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM goals GROUP BY status ORDER BY COUNT(*) DESC;\""),
    ("ACTIVE by stage",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT metadata->>'execution_stage' as stage, COUNT(*) FROM goals WHERE status='ACTIVE' GROUP BY 1 ORDER BY 2 DESC;\""),
    ("ACHIEVED goals",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, original_input, metadata->>'execution_stage' as stage FROM goals WHERE status='ACHIEVED' ORDER BY updated_at DESC LIMIT 15;\""),
    ("EXHAUSTED goals",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, original_input, metadata->>'execution_stage' as stage FROM goals WHERE status='EXHAUSTED' ORDER BY updated_at DESC LIMIT 20;\""),
    ("Goal 6a286e4e full",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, status, original_input, metadata FROM goals WHERE id='6a286e4e-b049-457e-81e4-5a105c94ed06';\""),
    ("Dead letters",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, event_type, last_error, occurred_at::date FROM outbox_events WHERE status='DEAD_LETTER' ORDER BY occurred_at DESC;\""),
    ("Organizations",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, goal_id, strategy, status, rationale FROM organizations ORDER BY created_at DESC;\""),
    ("Works by goal status",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM works GROUP BY status ORDER BY COUNT(*) DESC;\""),
    ("Discovery rounds",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM discovery_rounds GROUP BY status ORDER BY 2 DESC;\""),
    ("Runs summary",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY 2 DESC;\""),
    ("Execution permits",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM execution_permits GROUP BY status ORDER BY 2 DESC;\""),
    ("Gate evaluations",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT decision, COUNT(*) FROM gate_evaluations GROUP BY decision ORDER BY 2 DESC;\""),
    ("Worker logs recent errors",
     "docker logs regent-worker --since 2h 2>&1 | grep -i 'error\|exception\|traceback\|fail' | tail -30"),
    ("Worker logs recent all",
     "docker logs regent-worker --since 2h 2>&1 | tail -30"),
    # Check Docker versions
    ("Docker images",
     "docker images | grep regent"),
    ("Docker ps",
     "docker ps --format '{{.Names}} {{.Status}}'"),
]

for label, cmd in queries:
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    out, err = run(cmd)
    if out.strip():
        print(out[:3000])
    if err.strip():
        print(f"STDERR: {err[:300]}")

ssh.close()
