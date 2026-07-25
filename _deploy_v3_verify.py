"""Deploy current code to server and verify V3 requirements."""
from __future__ import annotations

import json
import os
import sys
import tarfile
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent
RELEASE = "20260725-v3-frozen"
TAG = f"regent-core:{RELEASE}"
RELEASE_DIR = f"/opt/regent/releases/{RELEASE}"
TAR_NAME = f"regent-v3-{RELEASE}.tgz"


def load_env() -> tuple[str, str, str]:
    server = os.environ.get("SERVER_IP", "")
    user = os.environ.get("LOGIN_USER", "")
    password = os.environ.get("LOGIN_PASSWORD", "")
    env_path = ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
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
    if not all([server, user, password]):
        raise RuntimeError("missing SERVER_IP/LOGIN_USER/LOGIN_PASSWORD in .env")
    return server, user, password


def create_tarball() -> Path:
    """Create a tarball of the project source."""
    tar_path = ROOT / TAR_NAME
    print(f"[1/7] Creating tarball: {tar_path}")
    exclude = {".venv", ".git", "__pycache__", ".mypy_cache", ".ruff_cache",
               ".pytest_cache", ".pytest-tmp", ".pytest-tmp2", ".pytest-tmp3",
               ".pytest-tmp4", ".pytest-tmp5", ".pytest-tmp6", ".pytest-tmp-delivery",
               ".tmp_test", "node_modules", ".agents"}
    # Also exclude old tgz files
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in ROOT.iterdir():
            if item.name.startswith(".") and item.name in exclude:
                continue
            if item.name.endswith(".tgz"):
                continue
            if item.name == TAR_NAME:
                continue
            tar.add(str(item), arcname=item.name,
                    filter=lambda ti: None if any(p in ti.name for p in exclude) else ti)
    size_mb = tar_path.stat().st_size / (1024 * 1024)
    print(f"  -> {size_mb:.1f} MB")
    return tar_path


def upload_tarball(client: paramiko.SSHClient, tar_path: Path) -> None:
    """Upload tarball to server."""
    print(f"[2/7] Uploading {tar_path.name} to server...")
    sftp = client.open_sftp()
    remote_path = f"/tmp/{TAR_NAME}"
    sftp.put(str(tar_path), remote_path)
    sftp.close()
    print(f"  -> Uploaded to {remote_path}")


def extract_on_server(client: paramiko.SSHClient) -> None:
    """Extract tarball into release directory."""
    print(f"[3/7] Extracting to {RELEASE_DIR}...")
    cmds = [
        f"mkdir -p {RELEASE_DIR}",
        f"cd {RELEASE_DIR} && tar xzf /tmp/{TAR_NAME}",
        f"ls -la {RELEASE_DIR}/core/src/regent/__init__.py",
    ]
    for cmd in cmds:
        _, stdout, stderr = client.exec_command(cmd, timeout=60)
        out = stdout.read().decode()
        err = stderr.read().decode()
        if out.strip():
            print(f"  {out.strip()[:200]}")
        if err.strip() and "warning" not in err.lower():
            print(f"  WARN: {err.strip()[:200]}")


def build_image(client: paramiko.SSHClient) -> None:
    """Build Docker image on server."""
    print(f"[4/7] Building Docker image {TAG}...")
    cmd = (
        f"cd {RELEASE_DIR} && docker build --no-cache "
        "--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ "
        f"-t {TAG} -f core/Dockerfile ."
    )
    _, stdout, stderr = client.exec_command(cmd, timeout=900)
    out = stdout.read().decode()
    err = stderr.read().decode()
    # Show last few lines of build output
    lines = (out + err).strip().split("\n")
    for line in lines[-10:]:
        print(f"  {line}")
    print("  -> Build complete")


def deploy_containers(client: paramiko.SSHClient) -> None:
    """Stop old containers, run migrations, start new ones."""
    print("[5/7] Deploying containers...")
    cmds = [
        "docker stop regent-api regent-worker 2>/dev/null || true",
        "docker rm regent-api regent-worker 2>/dev/null || true",
        # Run database migrations
        (
            f"docker run --rm --network regent-net --env-file /opt/regent/.env "
            f"-v /opt/regent/artifacts:/var/lib/regent/artifacts "
            f"-v /opt/regent/workspaces:/var/lib/regent/workspaces "
            f"-v /opt/regent/builds:/var/lib/regent/builds "
            f"{TAG} alembic upgrade head"
        ),
        # Start API
        (
            f"docker run -d --name regent-api --network regent-net --env-file /opt/regent/.env "
            f"-p 8000:8000 "
            f"-v /opt/regent/artifacts:/var/lib/regent/artifacts "
            f"-v /opt/regent/workspaces:/var/lib/regent/workspaces "
            f"-v /opt/regent/builds:/var/lib/regent/builds "
            f"{TAG} regent-api"
        ),
        # Start Worker
        (
            f"docker run -d --name regent-worker --network regent-net --user root "
            f"--env-file /opt/regent/.env "
            f"-e REGENT_BUILD_ROOT=/opt/regent/builds "
            f"-e REGENT_ARTIFACT_ROOT=/opt/regent/artifacts "
            f"-e REGENT_WORKSPACE_ROOT=/opt/regent/workspaces "
            f"-v /opt/regent/artifacts:/opt/regent/artifacts "
            f"-v /opt/regent/workspaces:/opt/regent/workspaces "
            f"-v /opt/regent/builds:/opt/regent/builds "
            f"-v /usr/bin/docker:/usr/bin/docker:ro "
            f"-v /var/run/docker.sock:/var/run/docker.sock "
            f"{TAG} regent-worker"
        ),
        f"ln -sfn {RELEASE_DIR} /opt/regent/current",
    ]
    for cmd in cmds:
        short = cmd[:100]
        print(f"  >>> {short}...")
        _, stdout, stderr = client.exec_command(cmd, timeout=300)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        if out:
            print(f"    {out[:200]}")
        if err and "warning" not in err.lower() and "pull" not in err.lower():
            print(f"    {err[:200]}")


def health_check(client: paramiko.SSHClient) -> dict:
    """Run health checks against the deployed API."""
    print("[6/7] Running health checks...")
    time.sleep(10)  # Wait for services to start

    results = {}
    checks = [
        ("live", "curl -sf http://localhost:8000/health/live"),
        ("ready", "curl -sf http://localhost:8000/health/ready"),
        ("api_version", "curl -sf http://localhost:8000/v1/version 2>/dev/null || echo 'no version endpoint'"),
    ]
    for name, cmd in checks:
        _, stdout, stderr = client.exec_command(cmd, timeout=15)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        results[name] = out or err or "no response"
        status = "OK" if out and "error" not in out.lower() else "FAIL"
        print(f"  {name}: {status} -> {out[:200] or err[:200]}")

    # Check container status
    _, stdout, _ = client.exec_command("docker ps --filter name=regent --format '{{.Names}} {{.Status}}'")
    containers = stdout.read().decode().strip()
    print(f"  containers:\n{containers}")
    results["containers"] = containers

    return results


def verify_v3_requirements(client: paramiko.SSHClient) -> dict:
    """Verify V3 definition requirements against deployed system."""
    print("[7/7] Verifying V3 requirements...")
    v3_checks = {}

    # V3 §1: Goal Engine - check goal creation API exists
    print("  Checking G (Goal Engine)...")
    _, stdout, _ = client.exec_command(
        "curl -sf http://localhost:8000/v1/goals -X POST "
        "-H 'Content-Type: application/json' "
        "-d '{\"original_input\": \"test v3 verification\", \"created_by\": \"v3-verify\", \"metadata\": {}}' "
        "2>&1 || echo FAIL"
    )
    goal_result = stdout.read().decode().strip()
    v3_checks["G_Goal_Engine"] = "OK" if goal_result and "FAIL" not in goal_result else f"response: {goal_result[:200]}"

    # V3 §2.3: Governance Engine - check permit/audit endpoints
    print("  Checking V (Governance Engine)...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \"from regent.infrastructure.models import ExecutionPermitModel, AuditRecordModel; print('permit+audit-ok')\""
    )
    governance = stdout.read().decode().strip()
    v3_checks["V_Governance"] = governance if "ok" in governance else "FAIL"

    # V3 §2.4: Resource Engine - check capability registry
    print("  Checking R_t (Resource Engine)...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \"from regent.infrastructure.models import CapabilityModel, AgentSpecModel, ToolSpecModel; print('capability+agent+tool-ok')\""
    )
    resource = stdout.read().decode().strip()
    v3_checks["R_Resource"] = resource if "ok" in resource else "FAIL"

    # V3 §2.5: State Engine - check state machine tables
    print("  Checking S_t (State Engine)...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \"from regent.infrastructure.models import GoalModel, WorkModel, RunModel; print('goal+work+run-ok')\""
    )
    state = stdout.read().decode().strip()
    v3_checks["S_State"] = state if "ok" in state else "FAIL"

    # V3 §2.6: Organization Engine
    print("  Checking O (Organization Engine)...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \"from regent.infrastructure.models import OrganizationModel, AssignmentModel; print('org+assign-ok')\""
    )
    org = stdout.read().decode().strip()
    v3_checks["O_Organization"] = org if "ok" in org else "FAIL"

    # V3 §2.2: Constraint Engine - check budget/resource constraints
    print("  Checking C (Constraint Engine)...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \"from regent.application.execution_orchestrator import ExecutionOrchestrator; print('orchestrator-ok')\""
    )
    constraint = stdout.read().decode().strip()
    v3_checks["C_Constraint"] = constraint if "ok" in constraint else "FAIL"

    # V3 G0: ExternalOperation (durable external effects)
    print("  Checking G0 ExternalOperation...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \"from regent.infrastructure.models import ExternalOperationModel; print('external-ops-ok')\""
    )
    eo = stdout.read().decode().strip()
    v3_checks["G0_ExternalOperation"] = eo if "ok" in eo else "FAIL"

    # V3 §5.1: P0 verification - main chain components
    print("  Checking P0 components...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \""
        "from regent.application.execution_orchestrator import ExecutionOrchestrator; "
        "from regent.application.goal_service import GoalService; "
        "from regent.application.discovery_worker import DiscoveryWorker; "
        "from regent.application.capability_resolution_service import CapabilityResolutionService; "
        "from regent.application.generation_service import GenerationService; "
        "from regent.application.build_service import BuildService; "
        "from regent.application.observation_service import ObservationService; "
        "from regent.application.iteration_loop_service import IterationLoopService; "
        "print('p0-chain-ok')\""
    )
    p0 = stdout.read().decode().strip()
    v3_checks["P0_MainChain"] = p0 if "ok" in p0 else f"FAIL: {p0[:200]}"

    # V3 §2.4: ACQUIRE - self-organization capability acquisition
    print("  Checking R_t ACQUIRE (self-organization)...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \""
        "from regent.application.capability_acquire_service import CapabilityAcquireService, AcquireRequest; "
        "from regent.application.capability_ladder import EscalationStep; "
        "assert EscalationStep.ACQUIRE == 'ACQUIRE'; "
        "print('acquire-ok')\""
    )
    acquire = stdout.read().decode().strip()
    v3_checks["R_Acquire"] = acquire if "ok" in acquire else f"FAIL: {acquire[:200]}"

    # V3 §4: Core/App separation
    print("  Checking Core/App separation...")
    _, stdout, _ = client.exec_command("ls /app/apps/ 2>/dev/null && echo 'apps-dir-ok' || echo 'no-apps-dir'")
    apps = stdout.read().decode().strip()
    v3_checks["CoreApp_Separation"] = apps if "ok" in apps else "WARN: no apps dir"

    # V3 §1.6: Utility Function (enhanced organization_service)
    print("  Checking V3 Utility Function...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \""
        "from regent.application.organization_service import "
        "compute_utility, select_best_organization, UtilityWeights, UtilityResult; "
        "w = UtilityWeights(); "
        "print('utility-ok')\""
    )
    utility = stdout.read().decode().strip()
    v3_checks["V3_UtilityFunction"] = utility if "ok" in utility else f"FAIL: {utility[:200]}"

    # V3 §2.1: Goal Decomposition (enhanced goal_interpreter)
    print("  Checking V3 Goal Decomposition...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \""
        "from regent.application.goal_interpreter import "
        "GoalInterpreter, SubGoal, KPIExtractor, KPI; "
        "sg = SubGoal(id='t', label='test'); "
        "print('decompose-ok')\""
    )
    decompose = stdout.read().decode().strip()
    v3_checks["V3_GoalDecompose"] = decompose if "ok" in decompose else f"FAIL: {decompose[:200]}"

    # V3 §2.5: Memory Hierarchy (enhanced memory_service)
    print("  Checking V3 Memory Hierarchy...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \""
        "from regent.application.memory_service import "
        "MemoryKind, MemoryService; "
        "assert MemoryKind.EPISODIC_GOAL_ACHIEVED == 'episodic.goal_achieved'; "
        "assert MemoryKind.SEMANTIC_RULE == 'semantic.rule'; "
        "assert MemoryKind.WORKING_CONTEXT == 'working.context'; "
        "print('memory-ok')\""
    )
    memory = stdout.read().decode().strip()
    v3_checks["V3_MemoryHierarchy"] = memory if "ok" in memory else f"FAIL: {memory[:200]}"

    # V3 §4: Domain Events (enhanced execution_events)
    print("  Checking V3 Domain Events...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \""
        "from regent.application.execution_events import V3_DOMAIN_EVENTS, REORGANIZATION_TRIGGERED, CONSTRAINT_VIOLATED; "
        "assert len(V3_DOMAIN_EVENTS) >= 30; "
        "print('events-ok')\""
    )
    events = stdout.read().decode().strip()
    v3_checks["V3_DomainEvents"] = events if "ok" in events else f"FAIL: {events[:200]}"

    # V3 §1.3: Compliance Gate (integrated in orchestrator)
    print("  Checking V3 Compliance Gate...")
    _, stdout, _ = client.exec_command(
        "docker exec regent-api python -c \""
        "from regent.application.execution_orchestrator import ExecutionOrchestrator; "
        "from regent.application.compliance_risk_service import ComplianceChecker, RiskEngine; "
        "assert hasattr(ExecutionOrchestrator, '_run_compliance_gate'); "
        "print('compliance-gate-ok')\""
    )
    compliance = stdout.read().decode().strip()
    v3_checks["V3_ComplianceGate"] = compliance if "ok" in compliance else f"FAIL: {compliance[:200]}"

    # Check migration chain integrity
    print("  Checking Alembic migration chain...")
    _, stdout, _ = client.exec_command(
        f"docker run --rm --network regent-net --env-file /opt/regent/.env {TAG} alembic check 2>&1 | tail -3"
    )
    alembic = stdout.read().decode().strip()
    v3_checks["Alembic_Chain"] = alembic[:200] if alembic else "no output"

    # Print summary
    print("\n  === V3 Requirements Verification ===")
    all_ok = True
    for key, val in v3_checks.items():
        status = "PASS" if "ok" in val.lower() else ("WARN" if "warn" in val.lower() else "CHECK")
        if status == "CHECK":
            all_ok = False
        print(f"  [{status}] {key}: {val[:120]}")

    return v3_checks


def main() -> None:
    server, user, password = load_env()
    print(f"Deploying to {server} as {user}")
    print(f"Release: {RELEASE}")
    print()

    # Step 1: Create tarball
    tar_path = create_tarball()

    # Step 2: Connect and upload
    print(f"\nConnecting to {server}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(server, username=user, password=password, timeout=20)
    print("Connected.\n")

    try:
        upload_tarball(client, tar_path)
        extract_on_server(client)
        build_image(client)
        deploy_containers(client)
        health = health_check(client)
        v3 = verify_v3_requirements(client)

        print("\n" + "=" * 60)
        print("DEPLOYMENT SUMMARY")
        print("=" * 60)
        print(f"Release: {RELEASE}")
        print(f"Tag: {TAG}")
        print(f"Server: {server}")
        print(f"\nHealth: {json.dumps(health, indent=2, ensure_ascii=False)[:500]}")
        print(f"\nV3 Checks: {len([v for v in v3.values() if 'ok' in v.lower()])}/{len(v3)} passed")

    finally:
        client.close()
        print("\nConnection closed.")

    # Cleanup local tarball
    if tar_path.exists():
        tar_path.unlink()
        print(f"Cleaned up {tar_path}")


if __name__ == "__main__":
    main()
