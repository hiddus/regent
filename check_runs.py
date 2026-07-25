"""Check generation runs."""
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

print("=== Latest Generation Runs ===")
print(q("SELECT id, plan_id, status, failure_code, attempt, correlation_id FROM generation_runs ORDER BY created_at DESC LIMIT 10"))

print("\n=== Generation Plans (latest) ===")
print(q("SELECT id, status, version FROM generation_plans ORDER BY created_at DESC LIMIT 5"))

print("\n=== File Change Sets (latest) ===")
print(q("SELECT id, generation_run_id, generator_ref FROM file_change_sets ORDER BY created_at DESC LIMIT 5"))

# Check the failed generation plan's runs
plan_id = "e95ba492-c266-4dbf-ab6f-80ef49d97067"
print(f"\n=== Runs for plan {plan_id} ===")
print(q(f"SELECT id, status, failure_code FROM generation_runs WHERE plan_id='{plan_id}'"))

client.close()
