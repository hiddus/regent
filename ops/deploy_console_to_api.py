"""Copy regent-console dist/ into regent-api container at /app/apps/regent-console/."""
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


def build_dist_tar() -> bytes:
    """Package dist/ contents into a tarball."""
    buf = io.BytesIO()
    dist_dir = ROOT / "apps" / "regent-console" / "dist"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in dist_dir.rglob("*"):
            if path.is_file():
                arc = f"dist/{path.relative_to(dist_dir).as_posix()}"
                tar.add(path, arcname=arc)
    return buf.getvalue()


def main() -> None:
    payload = build_dist_tar()
    print(f"dist package bytes={len(payload)} sha={hashlib.sha256(payload).hexdigest()[:12]}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

    sftp = ssh.open_sftp()
    with sftp.file("/tmp/regent-console-dist.tgz", "wb") as f:
        f.write(payload)
    sftp.close()
    print("uploaded")

    # Extract and copy into regent-api container
    out, code = run(ssh, r"""
set -e
rm -rf /tmp/console-stage
mkdir -p /tmp/console-stage
tar -xzf /tmp/regent-console-dist.tgz -C /tmp/console-stage

# Copy into regent-api container at the dist/ path main.py expects
docker exec regent-api mkdir -p /app/apps/regent-console/dist
docker cp /tmp/console-stage/dist/. regent-api:/app/apps/regent-console/dist/

# Also copy to site-packages path for persistence
REL=$(readlink -f /opt/regent/current)
mkdir -p "$REL/apps/regent-console"
cp -a /tmp/console-stage/dist/. "$REL/apps/regent-console/"

echo COPY_OK
""")
    print(out.strip())
    if code != 0:
        ssh.close()
        raise SystemExit(1)

    # No restart needed — StaticFiles reads from disk on each request
    time.sleep(2)

    # Verify
    out, code = run(ssh, "curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/console/")
    print(f"console at /console/ HTTP status: {out.strip()}")

    out, code = run(ssh, "curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/")
    print(f"root / redirect HTTP status: {out.strip()}")

    # Check file exists inside container
    out, code = run(ssh, "docker exec regent-api ls -la /app/apps/regent-console/ | head -10")
    print(f"files in container:\n{out.strip()}")

    ssh.close()
    print("CONSOLE_DEPLOY_VIA_API_COMPLETE")


if __name__ == "__main__":
    main()
