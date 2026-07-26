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

# Full metadata for several PREVIEW_SUCCEEDED goals
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id, metadata::text
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
LIMIT 5;
" """
out, err = ssh_cmd(sql)
print("=== FULL METADATA SAMPLE ===")
print(out[:3000])

# Check gate-related keys in metadata
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT DISTINCT jsonb_object_keys(metadata) as key
FROM goals
WHERE metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
ORDER BY 1;
" """
out, err = ssh_cmd(sql)
print("\n=== METADATA KEYS ===")
print(out)

# Check delivery_review or acceptance or gate keys
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id,
       metadata->>'acceptance_result' as acceptance_result,
       metadata->>'acceptance_decision' as acceptance_decision,
       metadata->>'delivery_review_result' as delivery_review_result,
       metadata->>'gate_evaluation' as gate_evaluation,
       metadata->>'gate_summary' as gate_summary
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
LIMIT 10;
" """
out, err = ssh_cmd(sql)
print("\n=== GATE-RELATED FIELDS ===")
print(out)

# Check for evaluation/pass/fail/evidence keys
sql = """docker exec regent-postgres psql -U regent -d regent -c "
SELECT id,
       metadata->>'convergence_result' as conv_result,
       metadata->>'milestone_index' as milestone_idx,
       metadata->>'milestone_total' as milestone_total,
       metadata->>'convergence_decision' as conv_decision
FROM goals
WHERE status='ACTIVE' AND metadata->>'execution_stage' = 'PREVIEW_SUCCEEDED'
LIMIT 10;
" """
out, err = ssh_cmd(sql)
print("\n=== CONVERGENCE FIELDS ===")
print(out)

ssh.close()
