"""Check delivery gap reasons."""
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

print("=== Generation Runs ===")
print(q(f"SELECT id, status, failure_code FROM generation_runs WHERE correlation_id='{GOAL_ID}'"))

print("\n=== File Change Sets ===")
print(q(f"""SELECT fcs.id, fcs.generator_ref, fcs.prompt_version 
FROM file_change_sets fcs 
JOIN generation_runs gr ON fcs.generation_run_id = gr.id 
WHERE gr.correlation_id='{GOAL_ID}'"""))

print("\n=== Generation Plan ===")
print(q(f"""SELECT gp.id, gp.status, gp.planned_paths 
FROM generation_plans gp 
JOIN requirement_revisions rr ON gp.requirement_revision_id = rr.id 
WHERE rr.goal_id='{GOAL_ID}'"""))

print("\n=== Outbox Events ===")
print(q(f"SELECT event_type, payload->>'delivery_gap_reasons' FROM outbox_events WHERE aggregate_id='{GOAL_ID}' AND event_type LIKE '%DELIVERY%'"))

print("\n=== Worker Logs (delivery gap) ===")
_, stdout, _ = client.exec_command("docker logs regent-worker 2>&1 | grep -i 'delivery\\|gap\\|reject\\|fail' | tail -30", timeout=30)
print(stdout.read().decode().strip())

client.close()
