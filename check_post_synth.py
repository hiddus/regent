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

# Full status check
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) as cnt FROM goals GROUP BY status ORDER BY cnt DESC;
" """)
print("=== GOAL STATUSES ===")
print(out)

# Check remaining PREVIEW_SUCCEEDED details
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT id, metadata->>'last_gate_status' as gate, metadata->>'last_iteration_decision' as decision,
       metadata->>'last_deployment_id' as deploy_id
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED';
" """)
print("=== REMAINING PREVIEW_SUCCEEDED ===")
print(out)

# What stages are ACTIVE goals in now?
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COALESCE(metadata->>'execution_stage', 'NULL') as stage, COUNT(*)
FROM goals WHERE status='ACTIVE' GROUP BY stage ORDER BY COUNT(*) DESC;
" """)
print("=== ACTIVE STAGES ===")
print(out)

# Check for newly ACHIEVED/EXHAUSTED goals
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT id, status, metadata->>'execution_stage' as stage, metadata->>'last_gate_status' as gate
FROM goals
WHERE (status IN ('ACHIEVED', 'EXHAUSTED'))
  AND metadata->>'repair_context' IS NOT NULL
LIMIT 20;
" """)
print("=== REPAIR-CONTEXT GOALS ===")
print(out)

# Wait more and recheck
time.sleep(10)
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) as cnt FROM goals GROUP BY status ORDER BY cnt DESC;
" """)
print("\n=== GOAL STATUSES (after wait) ===")
print(out)

# Check outbox status
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT status, COUNT(*) FROM outbox_events WHERE event_type='PreviewDeploymentSucceeded' AND status='PENDING';
" """)
print("=== PENDING PREVIEW EVENTS ===")
print(out)

# Final worker logs
out, err = run("docker logs regent-worker --since 20s 2>&1 | tail -15")
print("=== FINAL WORKER LOGS ===")
print(out.strip()[:1000])

ssh.close()
