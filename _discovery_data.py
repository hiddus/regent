"""Check discovery evidence and selection data."""
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

# 1. Check all tables related to discovery/hypotheses
run("Discovery Tables",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE '%hypoth%' OR table_name LIKE '%discover%' OR table_name LIKE '%evidence%' OR table_name LIKE '%research%') ORDER BY table_name\"")

# 2. Check discovery round details - output/evidence
run("Discovery Round Output",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, goal_id, round, status, failure_code FROM discovery_rounds WHERE goal_id='c197690d-d80c-4589-80bb-c2ecb67fd4e7' ORDER BY round\"")

# 3. Check the goal spec for latest goal
run("Latest Goal Spec",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, status, intent FROM goal_specs WHERE id='c197690d-d80c-4589-80bb-c2ecb67fd4e7'\"")

# 4. Check evidence/acquisition tables
run("Evidence Tables",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE '%acquis%' OR table_name LIKE '%finding%') ORDER BY table_name\"")

# 5. List all public tables
run("All Tables",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name\"")

client.close()
