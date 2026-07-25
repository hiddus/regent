import os
import time

import paramiko

RELEASE = "20260721-p1-0022-r26"
TAG = f"regent-core:{RELEASE}"
ROOT = f"/opt/regent/releases/{RELEASE}"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    "118.31.171.159",
    username="root",
    password=os.environ["LOGIN_PASSWORD"],
    timeout=15,
)

# Ensure release exists from prior tar if missing; otherwise package is expected already uploaded
_, out, _ = client.exec_command(f"test -d {ROOT}/core && echo OK || echo MISSING")
print("release", out.read().decode().strip())

cmds = [
    (
        f"cd {ROOT} && docker build --no-cache "
        "--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ "
        f"-t {TAG} -f core/Dockerfile ."
    ),
    "docker stop regent-api regent-worker 2>/dev/null || true",
    "docker rm regent-api regent-worker 2>/dev/null || true",
    (
        "docker run --rm --network regent-net --env-file /opt/regent/.env "
        "-v /opt/regent/artifacts:/var/lib/regent/artifacts "
        "-v /opt/regent/workspaces:/var/lib/regent/workspaces "
        f"-v /opt/regent/builds:/var/lib/regent/builds {TAG} alembic upgrade head"
    ),
    (
        "docker run -d --name regent-api --network regent-net --env-file /opt/regent/.env "
        "-p 8000:8000 -v /opt/regent/artifacts:/var/lib/regent/artifacts "
        "-v /opt/regent/workspaces:/var/lib/regent/workspaces "
        f"-v /opt/regent/builds:/var/lib/regent/builds {TAG} regent-api"
    ),
    (
        "docker run -d --name regent-worker --network regent-net --user root "
        "--env-file /opt/regent/.env "
        "-e REGENT_BUILD_ROOT=/opt/regent/builds "
        "-e REGENT_ARTIFACT_ROOT=/opt/regent/artifacts "
        "-e REGENT_WORKSPACE_ROOT=/opt/regent/workspaces "
        "-v /opt/regent/artifacts:/opt/regent/artifacts "
        "-v /opt/regent/workspaces:/opt/regent/workspaces "
        "-v /opt/regent/builds:/opt/regent/builds "
        "-v /usr/bin/docker:/usr/bin/docker:ro "
        f"-v /var/run/docker.sock:/var/run/docker.sock {TAG} regent-worker"
    ),
    f"ln -sfn {ROOT} /opt/regent/current",
]
for cmd in cmds:
    print(">>>", cmd[:100])
    _, stdout, stderr = client.exec_command(cmd, timeout=700)
    text = stdout.read().decode() + stderr.read().decode()
    print(text[-1200:] if len(text) > 1200 else text)
    print("---")

time.sleep(8)
_, stdout, _ = client.exec_command("curl -s http://localhost:8000/health/ready")
print("ready", stdout.read().decode())
_, stdout, stderr = client.exec_command(
    "docker exec regent-worker python -c "
    "\"from regent.infrastructure.deployment import stamp_preview_deployment_id; print('stamp-ok')\""
)
print(stdout.read().decode(), stderr.read().decode())
client.close()
