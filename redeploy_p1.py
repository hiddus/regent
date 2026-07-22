"""Rebuild and redeploy P1 acceptance."""
import os
import paramiko
import tarfile
import tempfile
import time
from pathlib import Path


def _load_deploy_config() -> tuple[str, str, str, str]:
    """Load server credentials from environment or local .env (never commit secrets)."""
    server = os.environ.get("SERVER_IP", "")
    user = os.environ.get("LOGIN_USER", "")
    password = os.environ.get("LOGIN_PASSWORD", "")
    release_tag = os.environ.get("REGENT_RELEASE_TAG", "20260721-p1-0022-r18")

    if not all([server, user, password]):
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key == "SERVER_IP" and not server:
                    server = value
                elif key == "LOGIN_USER" and not user:
                    user = value
                elif key == "LOGIN_PASSWORD" and not password:
                    password = value

    if not all([server, user, password]):
        raise RuntimeError(
            "Set SERVER_IP, LOGIN_USER, and LOGIN_PASSWORD via environment or local .env"
        )
    return server, user, password, release_tag


REMOTE_DIR = "/opt/regent"


def ssh_exec(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    print(f"\n>>> {cmd[:120]}...")
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print(out[-500:] if len(out) > 500 else out)
    if err and "WARNING" not in err and "DEPRECATED" not in err:
        print(f"STDERR: {err[-300:]}")
    return out


def main() -> None:
    server, user, password, release_tag = _load_deploy_config()
    project_root = Path(__file__).resolve().parent
    archive_path = Path(tempfile.gettempdir()) / f"regent-{release_tag}.tgz"

    print(f"Packaging to {archive_path}...")
    with tarfile.open(archive_path, "w:gz") as tar:
        for item in project_root.iterdir():
            if item.name.startswith(".") or item.name in (
                ".venv",
                ".mypy_cache",
                ".pytest_cache",
                ".ruff_cache",
            ):
                continue
            if item.suffix == ".tgz":
                continue
            if item.name in (
                "ssh_check.py",
                "deploy_p1.py",
                "redeploy_p1.py",
                "acceptance_test.py",
                "db_check.py",
            ):
                continue
            tar.add(str(item), arcname=f"regent/{item.name}")
    print(f"  Size: {archive_path.stat().st_size / 1024 / 1024:.1f} MB")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(server, username=user, password=password, timeout=15)

    remote_path = f"/tmp/regent-{release_tag}.tgz"
    print(f"Transferring to {remote_path}...")
    sftp = client.open_sftp()
    sftp.put(str(archive_path), remote_path)
    sftp.close()

    release_dir = f"{REMOTE_DIR}/releases/{release_tag}"
    image_tag = f"regent-core:{release_tag}"
    ssh_exec(client, f"mkdir -p {release_dir}")
    ssh_exec(client, f"tar xzf {remote_path} -C {release_dir} --strip-components=1")

    print("\nBuilding Docker images...")
    ssh_exec(
        client,
        f"cd {release_dir} && docker build "
        f"--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ "
        f"-t {image_tag} -f core/Dockerfile .",
        timeout=600,
    )
    # Build sandbox and resolver images for isolated build
    ssh_exec(
        client,
        f"cd {release_dir}/capabilities/bootstrap/sandbox && docker build "
        f"-t regent-python-web-v1-sandbox:1 .",
        timeout=300,
    )
    ssh_exec(
        client,
        f"cd {release_dir}/capabilities/bootstrap/resolver && docker build "
        f"-t regent-python-web-v1-resolver:1 .",
        timeout=300,
    )

    _, stdout, _ = client.exec_command("cat /opt/regent/.deploy.env")
    deploy_env = stdout.read().decode()
    db_pass = ""
    for line in deploy_env.splitlines():
        if line.startswith("REGENT_DB_PASSWORD="):
            db_pass = line.split("=", 1)[1]
    _, stdout, _ = client.exec_command("cat /opt/regent/.runtime.env")
    runtime_env = stdout.read().decode()
    obs_key = exp_key = api_key = ""
    for line in runtime_env.splitlines():
        if line.startswith("REGENT_OBSERVATION_SIGNING_KEY="):
            obs_key = line.split("=", 1)[1]
        elif line.startswith("REGENT_EXPERIMENT_SIGNING_KEY="):
            exp_key = line.split("=", 1)[1]
        elif line.startswith("REGENT_MODEL_API_KEY="):
            api_key = line.split("=", 1)[1]

    env_content = f"""REGENT_ENVIRONMENT=production
REGENT_DATABASE_URL=postgresql+psycopg://regent:{db_pass}@regent-postgres:5432/regent
REGENT_LOG_LEVEL=INFO
REGENT_WORKER_POLL_SECONDS=1.0
REGENT_WORKER_LEASE_SECONDS=30
REGENT_WORKSPACE_ROOT=/var/lib/regent/workspaces
REGENT_BUILD_ROOT=/var/lib/regent/builds
REGENT_ARTIFACT_ROOT=/var/lib/regent/artifacts
REGENT_MODEL_PROVIDER=openai-compatible
REGENT_MODEL_BASE_URL=https://api.deepseek.com
REGENT_MODEL_NAME=deepseek-v4-pro
REGENT_OBSERVATION_SIGNING_KEY={obs_key}
REGENT_EXPERIMENT_SIGNING_KEY={exp_key}
REGENT_MODEL_API_KEY={api_key}
REGENT_DEPENDENCY_EGRESS_PROXY=http://regent-egress:3128
"""
    ssh_exec(client, f"cat > {REMOTE_DIR}/.env << 'ENVEOF'\n{env_content}ENVEOF")

    # Ensure dependency egress proxy is available on regent-net
    ssh_exec(client, "docker rm -f regent-egress 2>/dev/null || true")
    ssh_exec(
        client,
        "docker run -d --name regent-egress --network regent-net "
        "--restart unless-stopped "
        "sameersbn/squid:3.5.27-2",
        timeout=180,
    )

    ssh_exec(client, "docker stop regent-api regent-worker 2>/dev/null || true")
    ssh_exec(client, "docker rm regent-api regent-worker 2>/dev/null || true")

    print("\nRunning migrations before service startup...")
    ssh_exec(
        client,
        f"docker run --rm --network regent-net --env-file {REMOTE_DIR}/.env "
        f"-v {REMOTE_DIR}/artifacts:/var/lib/regent/artifacts "
        f"-v {REMOTE_DIR}/workspaces:/var/lib/regent/workspaces "
        f"-v {REMOTE_DIR}/builds:/var/lib/regent/builds "
        f"{image_tag} alembic upgrade head",
    )

    ssh_exec(
        client,
        f"docker run -d --name regent-api --network regent-net "
        f"--env-file {REMOTE_DIR}/.env -p 8000:8000 "
        f"-v {REMOTE_DIR}/artifacts:/var/lib/regent/artifacts "
        f"-v {REMOTE_DIR}/workspaces:/var/lib/regent/workspaces "
        f"-v {REMOTE_DIR}/builds:/var/lib/regent/builds "
        f"{image_tag} regent-api",
    )
    # Worker needs docker CLI + socket access and matching host paths for sandbox mounts
    ssh_exec(
        client,
        f"docker run -d --name regent-worker --network regent-net "
        f"--user root "
        f"--env-file {REMOTE_DIR}/.env "
        f"-e REGENT_BUILD_ROOT=/opt/regent/builds "
        f"-e REGENT_ARTIFACT_ROOT=/opt/regent/artifacts "
        f"-e REGENT_WORKSPACE_ROOT=/opt/regent/workspaces "
        f"-v /opt/regent/artifacts:/opt/regent/artifacts "
        f"-v /opt/regent/workspaces:/opt/regent/workspaces "
        f"-v /opt/regent/builds:/opt/regent/builds "
        f"-v /usr/bin/docker:/usr/bin/docker:ro "
        f"-v /var/run/docker.sock:/var/run/docker.sock "
        f"{image_tag} regent-worker",
    )

    print("\nWaiting for startup...")
    time.sleep(10)

    ssh_exec(client, "curl -s http://localhost:8000/health/live")
    ssh_exec(client, "curl -s http://localhost:8000/health/ready")

    ssh_exec(client, f"ln -sfn {release_dir} {REMOTE_DIR}/current")
    ssh_exec(client, f"readlink -f {REMOTE_DIR}/current")

    ssh_exec(client, f"rm -f {remote_path}")

    client.close()
    print("\n" + "=" * 60)
    print(f"Deployed: {release_tag}")
    print("=" * 60)


if __name__ == "__main__":
    main()
