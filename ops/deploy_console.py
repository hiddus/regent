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

    # Verify standalone nginx console (:3000; may be firewalled externally)
    out, code = run(ssh, "curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/")
    print(f"console HTTP status: {out.strip()}")

    out, code = run(ssh, "docker ps --filter name=regent-console --format '{{.Names}} {{.Status}} {{.Ports}}'")
    print(f"container: {out.strip()}")

    # Public entry is regent-api StaticFiles at /console/ (port 8000).
    # deploy_console used to only refresh the :3000 nginx container, so users
    # hitting http://<host>:8000/console/ kept seeing a stale build.
    out, code = run(
        ssh,
        r"""
set -e
API_DIST=/app/apps/regent-console/dist
for c in regent-api; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "SKIP_API_CONSOLE=$c (not running)"
    continue
  fi
  docker exec "$c" mkdir -p "$API_DIST/assets"
  # Image files may be root-owned read-only; chmod before overwrite.
  docker exec "$c" sh -c "chmod -R u+w '$API_DIST' 2>/dev/null || true"
  # Overwrite in place (index.html + new hashed assets). Stale hashes can linger;
  # browsers follow index.html so that is enough for rollout.
  docker cp /tmp/console-deploy/dist/. "$c:$API_DIST/"
  docker exec "$c" sh -c "chmod -R a+rX '$API_DIST' 2>/dev/null || true"
  echo "API_CONSOLE_SYNCED=$c"
  docker exec "$c" sh -c "ls -la $API_DIST; ls -la $API_DIST/assets | tail -10"
done
# StaticFiles mount can 404 until process reload after directory overwrite.
docker restart regent-api
echo API_RESTARTED
""",
    )
    print(out.strip())
    if code != 0:
        print(f"api console sync failed with code {code}")
        ssh.close()
        raise SystemExit(1)

    # Wait for API to accept connections again.
    out, code = run(
        ssh,
        r"""
set -e
for i in 1 2 3 4 5 6 7 8 9 10 11 12 15; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8000/v1/health || echo 000)
  echo wait_api_$i=$code
  if [ "$code" = "200" ]; then break; fi
  sleep 2
done
curl -sS --max-time 5 -o /tmp/console_index.html http://127.0.0.1:8000/console/
ASSET=$(grep -oE 'assets/index-[A-Za-z0-9_.-]+\.js' /tmp/console_index.html | head -1)
echo ASSET=$ASSET
test -n "$ASSET"
curl -sS --max-time 10 -o /tmp/console_app.js "http://127.0.0.1:8000/console/$ASSET"
grep -F agent-roster /tmp/console_app.js >/dev/null && echo VERIFY_agent-roster=yes || echo VERIFY_agent-roster=no
grep -F '参与 Agent' /tmp/console_app.js >/dev/null && echo VERIFY_cn_title=yes || echo VERIFY_cn_title=no
wc -c /tmp/console_app.js
""",
    )
    print(out.strip())
    if "VERIFY_agent-roster=no" in out or "VERIFY_cn_title=no" in out:
        ssh.close()
        raise SystemExit("public /console/ still missing agent roster strings")

    ssh.close()
    print("CONSOLE_DEPLOY_COMPLETE")


if __name__ == "__main__":
    main()
