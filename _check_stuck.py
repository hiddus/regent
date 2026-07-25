"""Check deployment and delivery review failure."""
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
        print(out[-5000:] if len(out) > 5000 else out)
    if err and "WARNING" not in err and "DEPRECATED" not in err:
        print(f"STDERR: {err[-1000:]}")

# 1. Evidence table schema
run("Evidence Schema",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='evidence' ORDER BY ordinal_position\"")

# 2. Evidence for goal 5aa76d31
run("Evidence for 5aa76d31",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT * FROM evidence WHERE goal_id='5aa76d31-d36f-46ee-8754-2d20d15becbc' ORDER BY created_at DESC\"")

# 3. Deployment via correlation_id
run("Deployment via correlation",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT * FROM deployments WHERE correlation_id LIKE '%5aa76d31%' ORDER BY created_at DESC LIMIT 1\"")

# 4. App builds via correlation_id
run("App Builds via correlation",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT * FROM app_builds WHERE correlation_id LIKE '%5aa76d31%' ORDER BY created_at DESC LIMIT 1\"")

# 5. Release candidates
run("Release Candidates Schema",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='release_candidates' ORDER BY ordinal_position\"")

# 6. Check release candidates for this goal
run("Release Candidates",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT * FROM release_candidates WHERE correlation_id LIKE '%5aa76d31%' ORDER BY created_at DESC LIMIT 1\"")

# 7. Check delivery review capability
run("Delivery Review Capability",
    "cat /opt/regent/capabilities/delivery-review-v1/capability.json")

# 8. Check worker logs for delivery/deployment errors
run("Worker Delivery Logs",
    "docker logs regent-worker --tail 200 2>&1 | grep -i -E 'delivery|deploy|review|reject|GAC-A4|first_deliverable|fail' | tail -30")

client.close()
