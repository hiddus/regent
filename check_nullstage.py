import paramiko

HOST = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=22, username=USER, password=PASSWORD)

def ssh_cmd(cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')

# Age distribution of NULL-stage goals
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    CASE
        WHEN created_at > NOW() - INTERVAL '1 hour' THEN '<1h'
        WHEN created_at > NOW() - INTERVAL '24 hours' THEN '1h-24h'
        WHEN created_at > NOW() - INTERVAL '7 days' THEN '1d-7d'
        ELSE '>7d'
    END as age,
    COUNT(*)
FROM goals
WHERE status='ACTIVE' AND (metadata->>'execution_stage') IS NULL
GROUP BY age
ORDER BY MIN(created_at);
" """
out, err = ssh_cmd(db_cmd)
print("=== NULL-STAGE GOAL AGE ===")
print(out)

# Check if they have works
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    CASE WHEN w.id IS NOT NULL THEN 'has_works' ELSE 'no_works' END as work_status,
    COUNT(DISTINCT g.id)
FROM goals g
LEFT JOIN works w ON w.goal_id = g.id
WHERE g.status='ACTIVE' AND (g.metadata->>'execution_stage') IS NULL
GROUP BY 1;
" """
out, err = ssh_cmd(db_cmd)
print("=== WORKS ASSOCIATION ===")
print(out)

# Check outbox events
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    CASE WHEN e.id IS NOT NULL THEN 'has_outbox' ELSE 'no_outbox' END as outbox_status,
    COUNT(DISTINCT g.id)
FROM goals g
LEFT JOIN outbox_events e ON (e.payload->>'goal_id')::text = g.id::text
WHERE g.status='ACTIVE' AND (g.metadata->>'execution_stage') IS NULL
GROUP BY 1;
" """
out, err = ssh_cmd(db_cmd)
print("=== OUTBOX ASSOCIATION ===")
print(out)

# Check app_project_id
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    CASE WHEN app_project_id IS NOT NULL THEN 'has_app_project' ELSE 'no_app_project' END as project_status,
    COUNT(*)
FROM goals
WHERE status='ACTIVE' AND (metadata->>'execution_stage') IS NULL
GROUP BY 1;
" """
out, err = ssh_cmd(db_cmd)
print("=== APP PROJECT ===")
print(out)

# Check metadata contents for these goals
db_cmd = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id, created_at::text, metadata->>'product_intent' as intent,
       metadata->>'execution_stage' as stage,
       app_project_id
FROM goals
WHERE status='ACTIVE' AND (metadata->>'execution_stage') IS NULL
LIMIT 10;
" """
out, err = ssh_cmd(db_cmd)
print("=== NULL-STAGE SAMPLE ===")
print(out)

ssh.close()
