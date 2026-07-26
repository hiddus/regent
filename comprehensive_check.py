import paramiko
import json
import time

hostname = '118.31.171.159'
username = 'root'
password = '080900.UI'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(hostname, username=username, password=password, port=22)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    return stdout.read().decode('utf-8', errors='ignore'), stderr.read().decode('utf-8', errors='ignore')

# 1. Overall goals stats
print("=" * 60)
print("1. GOALS OVERVIEW")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT status, COUNT(*) FROM goals GROUP BY status ORDER BY COUNT(*) DESC;"'
)
print(out)

# 2. ACHIEVED goals details
print("=" * 60)
print("2. ACHIEVED GOALS")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT id, status, name, created_at::date, metadata->>\'execution_stage\' as stage, '
    "metadata->>'gate_result' as gate_result, "
    "app_project_id IS NOT NULL as has_app "
    'FROM goals WHERE status=\'ACHIEVED\' ORDER BY created_at DESC LIMIT 15;"'
)
print(out)

# 3. EXHAUSTED details
print("=" * 60)
print("3. EXHAUSTED GOALS")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT id, name, created_at::date, metadata->>\'execution_stage\' as stage, '
    "metadata->>'exhaustion_reason' as reason, "
    "metadata->>'gate_result' as gate_result "
    'FROM goals WHERE status=\'EXHAUSTED\' ORDER BY created_at DESC LIMIT 10;"'
)
print(out)

# 4. ACTIVE goals by stage
print("=" * 60)
print("4. ACTIVE GOALS BY STAGE")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT metadata->>\'execution_stage\' as stage, COUNT(*) '
    'FROM goals WHERE status=\'ACTIVE\' '
    'GROUP BY 1 ORDER BY 2 DESC;"'
)
print(out)

# 5. Dead letters
print("=" * 60)
print("5. DEAD LETTERS")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT event_type, COUNT(*) as cnt, MIN(created_at) as oldest, MAX(created_at) as newest '
    "FROM outbox_events WHERE status='DEAD_LETTER' "
    'GROUP BY event_type ORDER BY cnt DESC;"'
)
print(out)

# 6. Outbox events status
print("=" * 60)
print("6. OUTBOX STATUS")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT status, COUNT(*) FROM outbox_events GROUP BY status ORDER BY COUNT(*) DESC;"'
)
print(out)

# 7. Organizations
print("=" * 60)
print("7. ORGANIZATIONS")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT id, name, creation_strategy, created_at::date, metadata '
    'FROM organizations ORDER BY created_at DESC LIMIT 10;"'
)
print(out)

# 8. Goals with org
print("=" * 60)
print("8. GOALS WITH ORG")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT COUNT(*) as goals_with_org FROM goals WHERE organization_id IS NOT NULL;"'
)
print(out)

# 9. App projects
print("=" * 60)
print("9. APP PROJECTS")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT status, COUNT(*) FROM app_projects GROUP BY status ORDER BY COUNT(*) DESC;"'
)
print(out)

# 10. Worker logs (last 30 min)
print("=" * 60)
print("10. WORKER LOGS (LAST 30 MIN)")
print("=" * 60)
out, err = run(
    "docker logs regent-worker --since 30m 2>&1 | tail -50"
)
print(out)

# 11. New goal 6a286e4e status
print("=" * 60)
print("11. GOAL 6a286e4e STATUS")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT id, status, name, updated_at, metadata->>\'execution_stage\' as stage, '
    "metadata->>'gate_result' as gate_result, "
    "organization_id IS NOT NULL as has_org "
    'FROM goals WHERE id=\'6a286e4e\';"'
)
print(out)

# 12. Works for new goal
print("=" * 60)
print("12. WORKS FOR 6a286e4e")
print("=" * 60)
out, err = run(
    "docker exec regent-postgres psql -U regent -d regent -c "
    '"SELECT work_type, status, COUNT(*) FROM works '
    "WHERE goal_id='6a286e4e' GROUP BY work_type, status ORDER BY work_type, status;\""
)
print(out)

# 13. API health check
print("=" * 60)
print("13. API HEALTH")
print("=" * 60)
out, err = run("curl -s http://localhost:8000/v1/health 2>&1")
print(out)

ssh.close()
