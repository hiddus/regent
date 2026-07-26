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

# Detailed classification of PREVIEW_SUCCEEDED goals
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    COALESCE(metadata->>'gate_result', 'NULL') as gate_result,
    COALESCE(metadata->>'gate_decision', 'NULL') as gate_decision,
    metadata->>'last_deployment_id' as deployment_id,
    metadata->>'goal_scale' as scale,
    COUNT(*)
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
GROUP BY 1,2,3,4
ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(sql)
print("=== PREVIEW_SUCCEEDED CLASSIFICATION ===")
print(out)

# Check which have deployment_id (needed for synthetic event)
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    CASE WHEN metadata->>'last_deployment_id' IS NOT NULL THEN 'has_deploy' ELSE 'no_deploy' END as deploy,
    COUNT(*)
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
GROUP BY 1;
" """
out, err = ssh_cmd(sql)
print("=== DEPLOYMENT_ID ===")
print(out)

# Check gate_decision more carefully
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    metadata->>'gate_result' as gate_result,
    metadata->>'gate_decision' as gate_decision,
    metadata->>'last_deployment_id' as deploy_id,
    metadata->>'goal_scale' as scale,
    id,
    created_at::text
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
ORDER BY created_at DESC
LIMIT 15;
" """
out, err = ssh_cmd(sql)
print("=== SAMPLE ===")
print(out)

# Check the orchestrator code to understand gate_decision values
# Look at what decisions lead to what states
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT DISTINCT
    metadata->>'gate_decision' as decision,
    metadata->>'gate_result' as result
FROM goals
WHERE metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
ORDER BY 1,2;
" """
out, err = ssh_cmd(sql)
print("=== GATE DECISIONS ===")
print(out)

ssh.close()
