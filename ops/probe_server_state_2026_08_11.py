"""Read-only snapshot of the deployed Regent host: containers, model env, goals, recent errors.

Written before touching production model config so the change has a documented
"before" state to compare against.

Usage:
  python ops/probe_server_state_2026_08_11.py
"""

from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

SCRIPT = r"""
set +e
echo "=== docker ps ==="
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'

echo
echo "=== model env (live containers, ids only) ==="
for C in regent-api regent-worker; do
  echo "--- $C ---"
  docker exec $C printenv REGENT_MODEL_BASE_URL REGENT_MODEL_NAME REGENT_MODEL_NAME_2 REGENT_MODEL_NAME_3 REGENT_MODEL_THINKING_MODE 2>/dev/null
done

echo
echo "=== secrets.env model ids ==="
grep -E '^REGENT_MODEL_(NAME|NAME_2|NAME_3|BASE_URL|PROVIDER)' /opt/regent/.secrets.env 2>/dev/null

echo
echo "=== .env / .runtime.env / .deploy.env model ids ==="
for F in /opt/regent/.env /opt/regent/.runtime.env /opt/regent/.deploy.env; do
  echo "--- $F ---"
  grep -E '^REGENT_MODEL_NAME' $F 2>/dev/null
done

echo
echo "=== api health ==="
curl -sS -m 20 http://127.0.0.1:8000/health/ready | head -c 1200; echo

echo
echo "=== goals summary ==="
docker exec regent-api python - <<'PY' 2>&1 | tail -40
import os, json
from sqlalchemy import create_engine, text
url = os.environ.get("REGENT_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
try:
    e = create_engine(url)
    with e.connect() as c:
        rows = c.execute(text(
            "select status, count(*) from goals group by status order by 2 desc"
        )).fetchall()
        print("goals_by_status", json.dumps([list(map(str, r)) for r in rows], ensure_ascii=False))
        rows = c.execute(text(
            "select id, status, left(coalesce(title, text, ''), 40) as t, updated_at "
            "from goals order by updated_at desc limit 8"
        )).fetchall()
        for r in rows:
            print("goal", " | ".join(map(str, r)))
except Exception as exc:
    print("db_probe_failed", type(exc).__name__, str(exc)[:300])
PY

echo
echo "=== worker log: recent model errors ==="
docker logs --tail 4000 regent-worker 2>&1 | grep -oE 'HTTP[^ ]* (40[0-9]|5[0-9][0-9])|status_code=[0-9]+|Payment Required|no access to model|402|403' | sort | uniq -c | sort -rn | head -20

echo
echo "=== worker log tail ==="
docker logs --tail 40 regent-worker 2>&1 | tail -40
"""


def main() -> int:
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing in .env")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        HOST,
        username=CFG.get("LOGIN_USER") or "root",
        password=PASSWORD,
        timeout=40,
        banner_timeout=120,
        auth_timeout=60,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        _, out, err = ssh.exec_command(f"bash -s <<'EOS'\n{SCRIPT}\nEOS", timeout=300)
        text = (out.read() + err.read()).decode("utf-8", "replace")
        code = out.channel.recv_exit_status()
    finally:
        ssh.close()
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
