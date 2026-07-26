import paramiko, uuid, json, time
from datetime import datetime, timezone

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def run(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    o = stdout.read().decode('utf-8', errors='replace')
    e = stderr.read().decode('utf-8', errors='replace')
    return o, e

# Step 1: Fetch goals
out, err = run("""docker exec regent-postgres psql -U regent -d regent -t -A -F '|' -c "
SELECT id, metadata->>'last_deployment_id', COALESCE(app_project_id::text, ''),
       metadata->>'last_gate_status', metadata->>'last_iteration_decision'
FROM goals
WHERE status='ACTIVE'
  AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
  AND metadata->>'last_deployment_id' IS NOT NULL
  AND metadata->>'last_deployment_id' != '';
" """)

lines = [l.strip() for l in out.split('\n') if l.strip() and '|' in l]
print(f"Found {len(lines)} goals with deployment_id")

# Step 2: Build inserts
inserts = []
for line in lines:
    parts = line.split('|')
    if len(parts) < 2:
        continue
    goal_id = parts[0].strip()
    deployment_id = parts[1].strip()
    app_project_id = parts[2].strip() if len(parts) > 2 else ''
    gate_status = parts[3].strip() if len(parts) > 3 else ''
    decision = parts[4].strip() if len(parts) > 4 else ''
    
    eid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    endpoint = f"https://preview.invalid/deploy:{goal_id}:{deployment_id[:12]}"
    
    payload = json.dumps({
        "actor": "system-repair-20260726",
        "goal_id": goal_id,
        "endpoint": endpoint,
        "deployment_id": deployment_id,
        "app_project_id": app_project_id,
        "synthetic": True,
        "repair_context": f"re-triggering gate for {gate_status}/{decision} goal"
    })
    
    inserts.append(
        f"INSERT INTO outbox_events (id, event_type, aggregate_type, aggregate_id, aggregate_version, payload, status, attempt, available_at, occurred_at, correlation_id) "
        f"VALUES ('{eid}', 'PreviewDeploymentSucceeded', 'Goal', '{goal_id}', 0, '{payload}'::jsonb, 'PENDING', 0, NOW(), NOW(), '{cid}');"
    )

sql_content = "\n".join(inserts)
print(f"Generated {len(inserts)} INSERTs")

# Step 3: Write to server and execute
sftp = ssh.open_sftp()
with sftp.file("/tmp/synth_events.sql", "w") as f:
    f.write(sql_content)
sftp.close()

out, err = run("docker cp /tmp/synth_events.sql regent-postgres:/tmp/synth_events.sql && echo 'OK'")
print("Copy:", out.strip())
out, err = run("docker exec regent-postgres psql -U regent -d regent -f /tmp/synth_events.sql 2>&1")
print("Result:", out.strip()[:300], err.strip()[:100])

# Step 4: Verify
out, err = run("docker exec regent-postgres psql -U regent -d regent -c 'SELECT status, COUNT(*) FROM outbox_events GROUP BY status;'")
print("\n=== OUTBOX STATUSES ===")
print(out.strip())

# Step 5: Wait and check worker
print("\nWaiting 10s for worker...")
time.sleep(10)

out, err = run("docker logs regent-worker --since 15s 2>&1 | tail -30")
print("=== WORKER LOGS ===")
print(out.strip()[:1500])

# Step 6: Check PREVIEW_SUCCEEDED count
out, err = run("""docker exec regent-postgres psql -U regent -d regent -c "
SELECT COUNT(*) FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED';
" """)
print("\n=== REMAINING PREVIEW_SUCCEEDED ===")
print(out.strip())

ssh.close()
