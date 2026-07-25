"""Check delivery review failure details."""
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

# 1. Goal first_deliverable
run("Goal First Deliverable",
    "docker exec regent-postgres psql -U regent -d regent -t -A -c "
    "\"SELECT metadata->>'first_deliverable' FROM goals WHERE id='5aa76d31-d36f-46ee-8754-2d20d15becbc'\"")

# 2. Check app_builds for this goal
run("App Builds",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT column_name FROM information_schema.columns WHERE table_name='app_builds' ORDER BY ordinal_position\"")

# 3. Check the build content
run("Build Content",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, goal_id, status, workspace_locator FROM app_builds WHERE goal_id='5aa76d31-d36f-46ee-8754-2d20d15becbc'\"")

# 4. Check generation runs
run("Generation Runs",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT id, goal_id, status, model_ref FROM generation_runs WHERE goal_id='5aa76d31-d36f-46ee-8754-2d20d15becbc'\"")

# 5. Check evidence for this goal (should have http-snapshots now)
run("Evidence Types",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT evidence_type, quality_tier, COUNT(*) FROM evidence WHERE goal_id='5aa76d31-d36f-46ee-8754-2d20d15becbc' GROUP BY evidence_type, quality_tier\"")

# 6. Check hypothesis decisions
run("Hypothesis Decision",
    "docker exec regent-postgres psql -U regent -d regent -c "
    "\"SELECT hd.decision, ph.candidate_key, ph.content_json->>'candidate_solution' as solution FROM hypothesis_decisions hd JOIN product_hypotheses ph ON hd.selected_hypothesis_id=ph.id WHERE hd.round_id IN (SELECT id FROM discovery_rounds WHERE goal_id='5aa76d31-d36f-46ee-8754-2d20d15becbc')\"")

client.close()
