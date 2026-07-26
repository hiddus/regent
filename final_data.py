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
    ("Gate evals columns",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='gate_evaluations' ORDER BY ordinal_position;\""),
    ("Gate evals",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT result, COUNT(*) FROM gate_evaluations GROUP BY result ORDER BY 2 DESC;\""),
    ("Iteration decisions",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT decision, COUNT(*) FROM iteration_decisions GROUP BY decision ORDER BY 2 DESC;\""),
    ("Builds",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM app_builds GROUP BY status ORDER BY 2 DESC;\""),
    ("Deployments",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM deployments GROUP BY status ORDER BY 2 DESC;\""),
    ("Capability resolutions",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM capability_resolution_plans GROUP BY status ORDER BY 2 DESC;\""),
    ("Human tasks",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM human_tasks GROUP BY status ORDER BY 2 DESC;\""),
    ("Evidence count",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT COUNT(*) FROM evidence;\""),
    ("Product hypotheses",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM product_hypotheses GROUP BY status ORDER BY 2 DESC;\""),
    ("Requirements",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM requirement_revisions GROUP BY status ORDER BY 2 DESC;\""),
    ("Generation runs",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM generation_runs GROUP BY status ORDER BY 2 DESC;\""),
    # Check specific stuck goals
    ("30 NULL-stage ACTIVE goals detail",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT id, original_input, metadata->>'last_execution_stage' as last_stage FROM goals WHERE status='ACTIVE' AND (metadata->>'execution_stage') IS NULL LIMIT 10;\""),
    ("Egress proxy check",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT COUNT(*) FROM external_operations;\""),
    ("Tables row counts",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 30;\""),
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
