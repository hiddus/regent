"""Deep-dive V3 verification on deployed server."""
from __future__ import annotations

import os
import paramiko


def load_env() -> tuple[str, str, str]:
    server = os.environ.get("SERVER_IP", "")
    user = os.environ.get("LOGIN_USER", "")
    password = os.environ.get("LOGIN_PASSWORD", "")
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k == "SERVER_IP" and not server:
                server = v
            elif k == "LOGIN_USER" and not user:
                user = v
            elif k == "LOGIN_PASSWORD" and not password:
                password = v
    return server, user, password


def run_cmd(client: paramiko.SSHClient, cmd: str, timeout: int = 30) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return (stdout.read().decode() + stderr.read().decode()).strip()


def main() -> None:
    server, user, password = load_env()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(server, username=user, password=password, timeout=20)
    print(f"Connected to {server}\n")

    # 1. Check Goal API - what endpoints exist?
    print("=" * 60)
    print("1. Goal API Investigation")
    print("=" * 60)
    r = run_cmd(client, "curl -sf http://localhost:8000/v1/goals 2>&1")
    print(f"  GET /v1/goals: {r[:300]}")
    r = run_cmd(client, "curl -sf http://localhost:8000/docs 2>&1 | head -5")
    print(f"  GET /docs: {'available' if r else 'not available'}")
    r = run_cmd(client, "curl -sf http://localhost:8000/openapi.json 2>&1 | python3 -c \"import sys,json; d=json.load(sys.stdin); print(sorted(d.get('paths',{}).keys()))\" 2>&1")
    print(f"  API paths: {r[:500]}")

    # 2. Check P0 main chain imports
    print("\n" + "=" * 60)
    print("2. P0 Main Chain Import Investigation")
    print("=" * 60)
    services = [
        ("execution_orchestrator", "from regent.application.execution_orchestrator import ExecutionOrchestrator"),
        ("goal_service", "from regent.application.goal_service import GoalService"),
        ("evidence_service", "from regent.application.evidence_service import EvidenceService"),
        ("hypothesis_service", "from regent.application.hypothesis_service import HypothesisService"),
        ("requirement_service", "from regent.application.requirement_service import RequirementService"),
        ("capability_resolution", "from regent.application.capability_resolution_service import CapabilityResolutionService"),
        ("generation_service", "from regent.application.generation_service import GenerationService"),
        ("build_service", "from regent.application.build_service import BuildService"),
        ("deployment_service", "from regent.application.deployment_service import DeploymentService"),
        ("observation_service", "from regent.application.observation_service import ObservationService"),
        ("gate_service", "from regent.application.gate_service import GateService"),
        ("iteration_loop", "from regent.application.iteration_loop_service import IterationLoopService"),
    ]
    for name, imp in services:
        r = run_cmd(client, f"docker exec regent-api python -c \"{imp}; print('ok')\" 2>&1")
        status = "OK" if "ok" in r else f"FAIL: {r[:200]}"
        print(f"  {name}: {status}")

    # 3. Check Alembic migration status
    print("\n" + "=" * 60)
    print("3. Alembic Migration Status")
    print("=" * 60)
    r = run_cmd(client, "docker exec regent-api alembic current 2>&1")
    print(f"  Current: {r[:300]}")
    r = run_cmd(client, "docker exec regent-api alembic history --verbose 2>&1 | tail -20")
    print(f"  History (last): {r[:500]}")

    # 4. Check apps directory in container
    print("\n" + "=" * 60)
    print("4. Apps Directory Check")
    print("=" * 60)
    r = run_cmd(client, "docker exec regent-api ls -la /app/apps/ 2>&1")
    print(f"  /app/apps/: {r[:300]}")

    # 5. Check all DB tables
    print("\n" + "=" * 60)
    print("5. Database Tables")
    print("=" * 60)
    r = run_cmd(client, """docker exec regent-postgres psql -U regent -d regent -c "\\dt" 2>&1 | tail -80""")
    print(f"  Tables:\n{r[:1000]}")

    # 6. Check worker logs for errors
    print("\n" + "=" * 60)
    print("6. Recent Worker Logs")
    print("=" * 60)
    r = run_cmd(client, "docker logs regent-worker --tail 20 2>&1")
    print(f"  {r[:800]}")

    # 7. Check API logs for errors
    print("\n" + "=" * 60)
    print("7. Recent API Logs")
    print("=" * 60)
    r = run_cmd(client, "docker logs regent-api --tail 20 2>&1")
    print(f"  {r[:800]}")

    client.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
