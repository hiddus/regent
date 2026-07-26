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

# 1. Find docker-compose
out, err = ssh_cmd("find /root -name 'docker-compose*' 2>/dev/null; find /opt -name 'docker-compose*' 2>/dev/null; ls /root/regent/ 2>/dev/null; ls /root/*.yml 2>/dev/null; ls /root/*.yaml 2>/dev/null")
print("=== FIND COMPOSE ===")
print(out)

# 2. Dead letters - simpler query
db_cmd = """docker exec regent-postgres psql -U regent -d regent -t -c "
SELECT e.id, e.payload->>'type' as event_type, e.error_message, e.last_error_time::text
FROM outbox_events e
WHERE e.status='DEAD_LETTER'
ORDER BY e.last_error_time DESC;
" """
out, err = ssh_cmd(db_cmd)
print("\n=== DEAD LETTERS ===")
print(out)

# 3. Check what domains are causing 403
out, err = ssh_cmd("docker logs regent-worker --tail 500 2>&1 | grep -B2 'http evidence fetch failed' | head -30")
print("\n=== EVIDENCE FETCH FAILS ===")
print(out)

# 4. Check what URLs are being fetched
out, err = ssh_cmd("docker logs regent-worker --tail 500 2>&1 | grep -i 'evidence.*url\|fetch.*http\|evidence_sources' | head -20")
print("\n=== EVIDENCE URLS ===")
print(out)

# 5. Find compose and read egress config
out, err = ssh_cmd("cat /root/regent/docker-compose.yml 2>/dev/null || cat /root/docker-compose*.yml 2>/dev/null")
print("\n=== DOCKER COMPOSE ===")
print(out[:3000] if out else "NOT FOUND")

# 6. Network check - what subnet is docker using
out, err = ssh_cmd("docker network inspect bridge 2>/dev/null | head -30; docker network ls")
print("\n=== DOCKER NETWORK ===")
print(out)

# 7. Check worker IP
out, err = ssh_cmd("docker inspect regent-worker --format '{{.NetworkSettings.IPAddress}}' 2>/dev/null")
print("\n=== WORKER IP ===")
print(out)

ssh.close()
