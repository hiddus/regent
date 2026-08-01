"""Clamp S0 generation strategy to plan freeze: artifact-backed, canary 0%."""
from __future__ import annotations

import time
from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]

REMOTE = r"""
set -e
ensure_kv() {
  local f="$1" k="$2" v="$3"
  touch "$f"
  if grep -q "^${k}=" "$f" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$f"
  else
    echo "${k}=${v}" >> "$f"
  fi
}
for ENVF in /opt/regent/.deploy.env /opt/regent/.runtime.env /opt/regent/.env; do
  ensure_kv "$ENVF" REGENT_GENERATION_STRATEGY artifact-backed
  ensure_kv "$ENVF" REGENT_GENERATION_STRATEGY_FALLBACK artifact-backed
  ensure_kv "$ENVF" REGENT_GENERATION_STRATEGY_CANARY_PERCENT 0
  ensure_kv "$ENVF" REGENT_GENERATION_STRATEGY_CANARY_GATE false
  ensure_kv "$ENVF" REGENT_GENERATION_STRATEGY_CANARY_VARIANT agentic
  ensure_kv "$ENVF" REGENT_GENERATION_STRATEGY_KILL_SWITCH false
done
echo '--- env after clamp ---'
grep -E 'GENERATION_STRATEGY' /opt/regent/.deploy.env /opt/regent/.env || true
# Prefer compose recreate when available; else restart.
if [ -f /opt/regent/current/compose.yaml ]; then
  cd /opt/regent/current
  docker compose --env-file /opt/regent/.deploy.env up -d --force-recreate api worker | tail -40
elif [ -f /opt/regent/compose.yaml ]; then
  cd /opt/regent
  docker compose --env-file /opt/regent/.deploy.env up -d --force-recreate api worker | tail -40
else
  docker restart regent-api
  docker ps -a --format '{{.Names}}' | grep -E '^regent-worker' | xargs -r docker restart
fi
sleep 10
echo '--- container env ---'
docker exec regent-api printenv | grep -E 'GENERATION_STRATEGY|CANARY' | sort || true
echo '--- settings ---'
docker exec regent-api python -c 'from regent.config import get_settings; s=get_settings(); print(s.generation_strategy, s.generation_strategy_canary_percent, s.generation_strategy_canary_gate)'
echo '--- health ---'
curl -s --max-time 8 http://127.0.0.1:8000/health/ready
echo
"""


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)
    _, o, e = ssh.exec_command(REMOTE, timeout=300)
    out = (o.read() + e.read()).decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    print(out)
    print("exit", code)
    ssh.close()
    if code != 0:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
