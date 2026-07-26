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
    ("Gate evals by status",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM gate_evaluations GROUP BY status ORDER BY 2 DESC;\""),
    ("Gate evals result_json sample",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, result_json->>'gate_result' as result, COUNT(*) FROM gate_evaluations GROUP BY 1,2 ORDER BY 3 DESC;\""),
    ("Observation types",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT observation_type, COUNT(*) FROM observations GROUP BY observation_type ORDER BY 2 DESC;\""),
    ("Observations table check",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='observations' ORDER BY ordinal_position;\""),
    ("Tool specs count",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT certification_status, COUNT(*) FROM tool_specs GROUP BY 1;\""),
    ("Agent specs",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM agent_specs GROUP BY 1;\""),
    ("Self improvement runs",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*) FROM self_improvement_runs GROUP BY 1;\""),
    ("Worker leases recent",
     "docker exec regent-postgres psql -U regent -d regent -c \"SELECT status, COUNT(*), MAX(updated_at) FROM worker_leases GROUP BY status;\""),
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
