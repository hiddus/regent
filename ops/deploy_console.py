"""Deploy the regent-console frontend to the production server.

Durable layout:
  - Host canonical tree: /opt/regent/console-dist  (survives container recreate)
  - Public entry: regent-api StaticFiles at /console/ (port 8000)
  - Optional: standalone nginx :3000

Recreate scripts (apply_model_from_secrets, etc.) must bind-mount
/opt/regent/console-dist -> /app/apps/regent-console/dist so the image
Jul-27 assets cannot roll back the UI again.
"""
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

HOST_CONSOLE_DIST = "/opt/regent/console-dist"
API_CONSOLE_DIST = "/app/apps/regent-console/dist"


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[str, int]:
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    return out, o.channel.recv_exit_status()


def build_console_tar() -> bytes:
    """Package dist/ + nginx.conf into a tarball."""
    buf = io.BytesIO()
    dist_dir = ROOT / "apps" / "regent-console" / "dist"
    nginx_conf = ROOT / "apps" / "regent-console" / "nginx.conf"
    if not (dist_dir / "index.html").is_file():
        raise SystemExit(f"missing console build: {dist_dir / 'index.html'}")

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in dist_dir.rglob("*"):
            if path.is_file():
                arc = f"console-dist/{path.relative_to(dist_dir).as_posix()}"
                tar.add(path, arcname=arc)
        if nginx_conf.is_file():
            tar.add(nginx_conf, arcname="console-nginx/nginx.conf")

    return buf.getvalue()


def main() -> None:
    payload = build_console_tar()
    print(f"console package bytes={len(payload)} sha={hashlib.sha256(payload).hexdigest()[:12]}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

    sftp = ssh.open_sftp()
    with sftp.file("/tmp/regent-console-deploy.tgz", "wb") as f:
        f.write(payload)
    sftp.close()
    print("uploaded to server")

    out, code = run(
        ssh,
        rf"""
set -e
rm -rf /tmp/console-deploy
mkdir -p /tmp/console-deploy/dist /tmp/console-deploy/nginx
tar -xzf /tmp/regent-console-deploy.tgz -C /tmp/console-deploy
if [ -d /tmp/console-deploy/console-dist ]; then
  cp -a /tmp/console-deploy/console-dist/. /tmp/console-deploy/dist/
fi
if [ -d /tmp/console-deploy/console-nginx ]; then
  cp -a /tmp/console-deploy/console-nginx/. /tmp/console-deploy/nginx/
fi

# 1) Durable host tree (source of truth across api recreates)
HOST_DIST={HOST_CONSOLE_DIST}
mkdir -p "$HOST_DIST"
find "$HOST_DIST" -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +
cp -a /tmp/console-deploy/dist/. "$HOST_DIST/"
chmod -R a+rX "$HOST_DIST"
echo HOST_CONSOLE_PERSISTED=$HOST_DIST
ls -la "$HOST_DIST" | head -5
ls -la "$HOST_DIST/assets" | tail -8

# Also mirror into release tree when present
if [ -L /opt/regent/current ] || [ -d /opt/regent/current ]; then
  REL=$(readlink -f /opt/regent/current 2>/dev/null || echo /opt/regent/current)
  mkdir -p "$REL/apps/regent-console/dist"
  rm -rf "$REL/apps/regent-console/dist"/*
  cp -a /tmp/console-deploy/dist/. "$REL/apps/regent-console/dist/"
  echo RELEASE_CONSOLE_SYNCED=$REL/apps/regent-console/dist
fi

# 2) Standalone nginx :3000 (optional / LAN)
if docker ps -a --format '{{{{.Names}}}}' | grep -q '^regent-console$'; then
  docker rm -f regent-console
fi
docker run -d \
  --name regent-console \
  --network regent-net \
  -p 3000:80 \
  -v {HOST_CONSOLE_DIST}:/usr/share/nginx/html:ro \
  -v /tmp/console-deploy/nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  --restart unless-stopped \
  nginx:1.27-alpine
echo CONTAINER_CREATED
""",
        timeout=120,
    )
    print(out.strip())
    if code != 0:
        print(f"deploy failed with code {code}")
        ssh.close()
        raise SystemExit(1)

    time.sleep(2)

    # 3) Ensure regent-api bind-mounts host console-dist (recreate if needed)
    out, code = run(
        ssh,
        rf"""
set -e
HOST_DIST={HOST_CONSOLE_DIST}
API_DIST={API_CONSOLE_DIST}
echo "CURRENT_MOUNTS:"
docker inspect regent-api --format '{{{{range .Mounts}}}}{{{{.Source}}}} -> {{{{.Destination}}}}{{{{println}}}}{{{{end}}}}' || true
MOUNTED=$(docker inspect regent-api --format '{{{{range .Mounts}}}}{{{{.Source}}}} -> {{{{.Destination}}}}{{{{println}}}}{{{{end}}}}' | grep -F "$API_DIST" || true)

if echo "$MOUNTED" | grep -q "$HOST_DIST"; then
  echo API_ALREADY_MOUNTED_HOST_CONSOLE
  docker restart regent-api
  echo API_RESTARTED
else
  echo API_NEEDS_CONSOLE_BIND_RECREATE
  python3 - <<'PY'
import json, subprocess
from pathlib import Path

HOST_DIST = "{HOST_CONSOLE_DIST}"
API_DIST = "{API_CONSOLE_DIST}"
name = "regent-api"
info = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
cfg = info["Config"]
host = info["HostConfig"]
env = {{}}
for item in cfg.get("Env") or []:
    if "=" in item:
        k, v = item.split("=", 1)
        env[k] = v
for path in (
    Path("/opt/regent/.runtime.env"),
    Path("/opt/regent/.deploy.env"),
    Path("/opt/regent/.secrets.env"),
    Path("/opt/regent/.env"),
):
    if not path.is_file():
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        env[k.strip()] = v.strip()

binds = list(host.get("Binds") or [])
filtered = []
for b in binds:
    parts = b.split(":")
    dest = parts[1] if len(parts) > 1 else ""
    if dest.rstrip("/") == API_DIST.rstrip("/"):
        continue
    filtered.append(b)
filtered.append(f"{{HOST_DIST}}:{{API_DIST}}:ro")

net = host.get("NetworkMode") or "regent-net"
subprocess.check_call(["docker", "rm", "-f", name])
cmd = ["docker", "run", "-d", "--name", name, "--network", net, "--restart", "unless-stopped"]
for b in filtered:
    cmd += ["-v", b]
for k, v in env.items():
    cmd += ["-e", f"{{k}}={{v}}"]
for p, hosts in (host.get("PortBindings") or {{}}).items():
    if hosts and hosts[0].get("HostPort"):
        cmd += ["-p", f"{{hosts[0]['HostPort']}}:{{p.split('/')[0]}}"]
if cfg.get("User"):
    cmd += ["--user", cfg["User"]]
if cfg.get("WorkingDir"):
    cmd += ["-w", cfg["WorkingDir"]]
cmd.append(cfg["Image"])
if cfg.get("Cmd"):
    cmd += list(cfg["Cmd"])
print("CREATE", name, "with console bind", filtered[-1])
subprocess.check_call(cmd)
print("API_RECREATED_WITH_CONSOLE_BIND")
PY
fi

if ! docker exec regent-api test -f {API_CONSOLE_DIST}/index.html; then
  echo FALLBACK_DOCKER_CP
  docker exec regent-api mkdir -p {API_CONSOLE_DIST}/assets
  docker exec regent-api sh -c "chmod -R u+w '{API_CONSOLE_DIST}' 2>/dev/null || true"
  docker cp /tmp/console-deploy/dist/. regent-api:{API_CONSOLE_DIST}/
fi
""",
        timeout=180,
    )
    print(out.strip())
    if code != 0:
        print(f"api console bind failed with code {code}")
        ssh.close()
        raise SystemExit(1)

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
echo "$ASSET" | grep -vq 'index-BvpzzeJ1' || { echo 'STALE_IMAGE_ASSET'; exit 1; }
curl -sS --max-time 10 -o /tmp/console_app.js "http://127.0.0.1:8000/console/$ASSET"
(grep -F agent-roster /tmp/console_app.js >/dev/null && echo VERIFY_agent-roster=yes) || echo VERIFY_agent-roster=no
(grep -F '总是允许' /tmp/console_app.js >/dev/null && echo VERIFY_always_allow=yes) || echo VERIFY_always_allow=no
(grep -F '参与 Agent' /tmp/console_app.js >/dev/null && echo VERIFY_cn_title=yes) || echo VERIFY_cn_title=no
docker inspect regent-api --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' | grep console-dist && echo VERIFY_BIND=yes || echo VERIFY_BIND=no
wc -c /tmp/console_app.js
docker exec regent-api printenv REGENT_MODEL_NAME
docker exec regent-api printenv REGENT_MODEL_BASE_URL
""",
        timeout=120,
    )
    print(out.strip())
    if code != 0 or "STALE_IMAGE_ASSET" in out or "VERIFY_BIND=no" in out:
        ssh.close()
        raise SystemExit("console deploy did not stick (stale asset, missing bind, or verify failed)")
    if "VERIFY_agent-roster=no" in out and "VERIFY_always_allow=no" in out:
        ssh.close()
        raise SystemExit("public /console/ missing expected UI markers")
    if "ASSET=" in out and "ASSET=\n" in out.replace("\r\n", "\n"):
        # empty asset line
        ssh.close()
        raise SystemExit("console index.html missing asset reference")

    ssh.close()
    print("CONSOLE_DEPLOY_COMPLETE")


if __name__ == "__main__":
    main()
