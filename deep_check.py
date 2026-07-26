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

# 1. Dead letter details
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT e.id, e.payload->>'type' as event_type, e.error_message, e.last_error_time::text,
       g.id as goal_id, g.status as goal_status, g.metadata->>'execution_stage' as stage
FROM outbox_events e
LEFT JOIN goals g ON (e.payload->>'goal_id')::text = g.id::text
WHERE e.status='DEAD_LETTER'
ORDER BY e.last_error_time DESC;
" """
out, err = ssh_cmd(db_cmd)
print("=== DEAD LETTER DETAILS ===")
print(out)

# 2. Egress squid config
out, err = ssh_cmd("docker exec regent-egress cat /etc/squid/squid.conf 2>/dev/null | head -80")
print("=== SQUID CONFIG ===")
print(out)

# 3. Check egress access list
out, err = ssh_cmd("docker exec regent-egress cat /etc/squid/squid.conf 2>/dev/null | grep -A5 -B5 -i 'acl\|http_access\|deny\|allow'")
print("=== SQUID ACL ===")
print(out)

# 4. Worker environment - proxy settings
out, err = ssh_cmd("docker exec regent-worker env 2>/dev/null | grep -i proxy")
print("=== WORKER PROXY ENV ===")
print(out)

# 5. Check API env for proxy settings
out, err = ssh_cmd("docker exec regent-api env 2>/dev/null | grep -i proxy")
print("=== API PROXY ENV ===")
print(out)

# 6. RUNNING runs detail
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT g.id as goal_id, g.status as goal_status, r.status as run_status,
       r.started_at::text, r.lease_expires_at::text,
       g.metadata->>'execution_stage' as stage
FROM runs r
JOIN works w ON r.work_id = w.id
JOIN goals g ON w.goal_id = g.id
WHERE r.status='RUNNING'
LIMIT 20;
" """
out, err = ssh_cmd(db_cmd)
print("=== RUNNING RUNS (sample) ===")
print(out)

# 7. Docker compose to find env vars
out, err = ssh_cmd("cat /root/docker-compose.yml 2>/dev/null || cat /root/regent/docker-compose.yml 2>/dev/null || find / -name 'docker-compose*' -maxdepth 3 2>/dev/null")
print("=== DOCKER COMPOSE LOCATION ===")
print(out[:500])

# 8. Check if egress port is accessible
out, err = ssh_cmd("docker exec regent-worker curl -x http://regent-egress:3128 http://httpbin.org/ip -m 5 2>&1 || echo 'PROXY_FAILED'")
print("=== PROXY TEST FROM WORKER ===")
print(out)

ssh.close()
