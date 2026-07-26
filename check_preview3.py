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

# Classify by last_gate_status and last_iteration_decision
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT
    COALESCE(metadata->>'last_gate_status', 'NULL') as gate_status,
    COALESCE(metadata->>'last_iteration_decision', 'NULL') as iter_decision,
    CASE WHEN metadata->>'last_deployment_id' IS NOT NULL THEN 'has_deploy' ELSE 'no_deploy' END as deploy,
    COUNT(*)
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
GROUP BY 1,2,3
ORDER BY COUNT(*) DESC;
" """
out, err = ssh_cmd(sql)
print("=== CLASSIFICATION ===")
print(out)

# Full metadata for each category (one sample)
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id,
       metadata->>'last_gate_status' as gate,
       metadata->>'last_iteration_decision' as decision,
       metadata->>'last_deployment_id' as deploy_id,
       metadata->>'milestones' as milestones,
       metadata->>'current_milestone_key' as cur_key,
       metadata->>'current_milestone_ordinal' as cur_ord,
       metadata->>'last_attained_milestone_ordinal' as last_ord,
       metadata->>'milestone_count' as total_ms
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
ORDER BY metadata->>'last_gate_status', metadata->>'last_iteration_decision'
LIMIT 20;
" """
out, err = ssh_cmd(sql)
print("\n=== DETAILED SAMPLE ===")
print(out)

# Check if PASSED+CONTINUE goals have milestones set
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id,
       metadata->>'last_gate_status' as gate,
       metadata->>'last_iteration_decision' as decision,
       metadata->>'current_milestone_key' as cur_key,
       metadata->>'current_milestone_ordinal' as cur_ord,
       metadata->>'milestone_count' as total_ms,
       metadata->>'first_deliverable' as deliverable
FROM goals
WHERE status='ACTIVE'
  AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
  AND metadata->>'last_gate_status' = 'PASSED'
  AND metadata->>'last_iteration_decision' = 'CONTINUE';
" """
out, err = ssh_cmd(sql)
print("\n=== PASSED+CONTINUE GOALS ===")
print(out)

ssh.close()
