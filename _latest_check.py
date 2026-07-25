"""Check latest APP status."""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("118.31.171.159", username="root", password="080900.UI", timeout=15)


def run(name, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"\n=== {name} ===")
    if out:
        print(out[-4000:] if len(out) > 4000 else out)
    if err and "WARNING" not in err and "DEPRECATED" not in err:
        print(f"STDERR: {err[-800:]}")


# Latest goals
run("Latest Goals",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, status, confirmed_at FROM goal_specs ORDER BY created_at DESC LIMIT 5\"")

# Latest app projects
run("Latest Projects",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, name, created_at FROM app_projects ORDER BY created_at DESC LIMIT 3\"")

# Latest outbox events (any new dead letters?)
run("New Dead Letters",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT event_type, status, aggregate_id, last_error FROM outbox_events WHERE status='DEAD_LETTER' AND occurred_at > '2026-07-25 11:10:00' ORDER BY occurred_at DESC LIMIT 5\"")

# Worker logs - most recent
run("Worker Logs (5min)", "docker logs --since 5m regent-worker 2>&1")

# Worker errors
run("Worker Errors", "docker logs --since 5m regent-worker 2>&1 | grep -i 'error\\|exception\\|traceback\\|failed' | tail -15")

# API errors
run("API Errors (5min)", "docker logs --since 5m regent-api 2>&1 | grep -i 'error\\|exception\\|traceback\\|failed' | tail -15")

# Health
run("Health",
    "docker exec regent-api python -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/ready').read().decode())\"")

# Latest conversation messages across all conversations
run("Latest Messages (all)",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT conversation_id, role, substring(content,1,100), created_at FROM conversation_messages ORDER BY created_at DESC LIMIT 10\"")

client.close()
