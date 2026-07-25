"""Check full chain for goal."""
import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

def q(sql):
    _, o, e = client.exec_command(f'{PSQL} -c "{sql}"', timeout=30)
    return o.read().decode().strip()

GOAL_ID = "622ad66b-3d2a-442a-82f3-570baaedd6f8"

print("=== Goal ===")
print(q(f"SELECT id, status, correlation_id FROM goals WHERE id='{GOAL_ID}'"))

print("\n=== Goal Spec ===")
print(q(f"SELECT id, status, version FROM goal_specs WHERE goal_id='{GOAL_ID}'"))

print("\n=== Discovery Rounds ===")
print(q(f"SELECT id, status, round FROM discovery_rounds WHERE goal_id='{GOAL_ID}'"))

print("\n=== Hypothesis Decisions ===")
print(q(f"""SELECT hd.decision, hd.rationale 
FROM hypothesis_decisions hd 
JOIN discovery_rounds dr ON hd.round_id = dr.id 
WHERE dr.goal_id='{GOAL_ID}'"""))

print("\n=== Requirement Revisions ===")
print(q(f"SELECT id, status FROM requirement_revisions WHERE goal_id='{GOAL_ID}'"))

print("\n=== Capability Resolution Plans ===")
print(q(f"""SELECT crp.id, crp.status 
FROM capability_resolution_plans crp 
JOIN requirement_revisions rr ON crp.requirement_revision_id = rr.id 
WHERE rr.goal_id='{GOAL_ID}'"""))

print("\n=== Generation Plans ===")
print(q(f"""SELECT gp.id, gp.status, gp.input_digest 
FROM generation_plans gp 
JOIN requirement_revisions rr ON gp.requirement_revision_id = rr.id 
WHERE rr.goal_id='{GOAL_ID}'"""))

print("\n=== Outbox Events ===")
print(q(f"SELECT event_type, status FROM outbox_events WHERE aggregate_id='{GOAL_ID}' ORDER BY occurred_at"))

print("\n=== Iteration Decisions ===")
print(q(f"SELECT id, decision, reason, milestone_key FROM iteration_decisions WHERE goal_id='{GOAL_ID}' ORDER BY created_at"))

client.close()
