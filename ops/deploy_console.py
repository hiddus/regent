"""Deploy the regent-console frontend to the production server."""
from __future__ import annotations

import hashlib
import io
import tarfile
import time
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = dotenv_values(ROOT / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[str, int]:
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    return out, o.channel.recv_exit_status()


def build_console_tar() -> bytes:
    """Package dist/ + nginx.conf into a tarball."""
    buf = io.BytesIO()
    dist_dir = ROOT / "apps" / "regent-console" / "dist"
    nginx_conf = ROOT / "apps" / "regent-console" / "nginx.conf"

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add dist contents
        for path in dist_dir.rglob("*"):
            if path.is_file():
                arc = f"console-dist/{path.relative_to(dist_dir).as_posix()}"
                tar.add(path, arcname=arc)
        # Add nginx.conf
        if nginx_conf.is_file():
            tar.add(nginx_conf, arcname="console-nginx/nginx.conf")

    return buf.getvalue()


def main() -> None:
    payload = build_console_tar()
    print(f"console package bytes={len(payload)} sha={hashlib.sha256(payload).hexdigest()[:12]}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

    # Upload
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/regent-console-deploy.tgz", "wb") as f:
        f.write(payload)
    sftp.close()
    print("uploaded to server")

    # Extract and check if console container exists
    out, code = run(ssh, r"""
set -e
rm -rf /tmp/console-deploy
mkdir -p /tmp/console-deploy/dist /tmp/console-deploy/nginx
tar -xzf /tmp/regent-console-deploy.tgz -C /tmp/console-deploy
# Move files to proper locations
if [ -d /tmp/console-deploy/console-dist ]; then
  cp -a /tmp/console-deploy/console-dist/. /tmp/console-deploy/dist/
fi
if [ -d /tmp/console-deploy/console-nginx ]; then
  cp -a /tmp/console-deploy/console-nginx/. /tmp/console-deploy/nginx/
fi

# Check if regent-console container exists
if docker ps -a --format '{{.Names}}' | grep -q '^regent-console$'; then
  echo "CONTAINER_EXISTS=yes, removing old one"
  docker rm -f regent-console
fi
# Create fresh container
docker run -d \
  --name regent-console \
  --network regent-net \
  -p 3000:80 \
  -v /tmp/console-deploy/dist:/usr/share/nginx/html:ro \
  -v /tmp/console-deploy/nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  --restart unless-stopped \
  nginx:1.27-alpine
echo "CONTAINER_CREATED"
""")
    print(out.strip())
    if code != 0:
        print(f"deploy failed with code {code}")
        # Try alternative network name
        out2, code2 = run(ssh, r"""
docker network ls --format '{{.Name}}' | head -5
docker ps --format '{{.Names}} {{.Networks}}' | head -10
""")
        print("networks:", out2.strip())
        ssh.close()
        raise SystemExit(1)

    time.sleep(3)

    # Verify
    out, code = run(ssh, "curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/")
    print(f"console HTTP status: {out.strip()}")

    out, code = run(ssh, "docker ps --filter name=regent-console --format '{{.Names}} {{.Status}} {{.Ports}}'")
    print(f"container: {out.strip()}")

    ssh.close()
    print("CONSOLE_DEPLOY_COMPLETE")


if __name__ == "__main__":
    main()
