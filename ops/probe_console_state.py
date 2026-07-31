"""Check whether GQ-3 recreate rolled back console static assets."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(Path(__file__).resolve().parents[1] / ".env").items()
}
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    CFG.get("SERVER_IP") or "118.31.171.159",
    username=CFG.get("LOGIN_USER") or "root",
    password=CFG["LOGIN_PASSWORD"],
    timeout=30,
)
cmd = r"""
set +e
echo '=== containers ==='
docker ps --format '{{.Names}} {{.Image}} {{.CreatedAt}} {{.Status}}'
echo '=== console paths on host ==='
ls -la /opt/regent/console 2>/dev/null | head -20
ls -la /opt/regent/current/console 2>/dev/null | head -10
ls -la /opt/regent/releases/*/console 2>/dev/null | head -20
echo '=== api image Created / mounts ==='
docker inspect regent-api --format 'Created={{.Created}} Image={{.Config.Image}}'
docker inspect regent-api --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
echo '=== static inside api ==='
docker exec regent-api sh -lc 'ls -la /app/console 2>/dev/null | head; ls -la /app/static 2>/dev/null | head; ls -la /usr/local/lib/python3.12/site-packages/regent/api/static 2>/dev/null | head; find /app -maxdepth 3 -type d -iname "*console*" 2>/dev/null | head'
echo '=== http console ==='
curl -sI --max-time 5 http://127.0.0.1:8000/console/ | head -15
curl -s --max-time 5 http://127.0.0.1:8000/console/ | head -c 400; echo
echo '=== recent console deploy markers ==='
ls -lt /opt/regent/console 2>/dev/null | head -15
stat /opt/regent/console/index.html 2>/dev/null || stat /opt/regent/console/dist/index.html 2>/dev/null || true
"""
_, o, e = ssh.exec_command(cmd, timeout=60)
print((o.read() + e.read()).decode("utf-8", "replace"))
ssh.close()
