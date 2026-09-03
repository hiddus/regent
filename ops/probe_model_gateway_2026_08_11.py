"""Probe the model gateway from the server for each configured model id.

The worker log shows `402 Payment Required` from
`https://ai.showmac.com/v1/chat/completions`. A 402 can be account-level (all
models dead) or model-level (one alias out of quota while others work), and the
fix differs, so probe every configured id. Runs curl on the host so the API key
never leaves it.

Usage:
  python ops/probe_model_gateway_2026_08_11.py
"""

from __future__ import annotations

import sys
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

REMOTE_SCRIPT = r"""
set -u
. /opt/regent/.secrets.env
BASE="${REGENT_MODEL_BASE_URL:-https://ai.showmac.com/v1}"
KEY="${REGENT_MODEL_API_KEY:-}"
echo "base=$BASE key_len=${#KEY}"

echo "=== GET /models ==="
curl -sS -m 30 -o /tmp/_models.json -w 'http=%{http_code}\n' \
  -H "Authorization: Bearer $KEY" "$BASE/models" || true
head -c 900 /tmp/_models.json; echo

for M in __MODEL_IDS__; do
  [ -z "$M" ] && continue
  echo "=== POST /chat/completions model=$M ==="
  curl -sS -m 60 -o /tmp/_chat.json -w 'http=%{http_code}\n' \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$M\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
    "$BASE/chat/completions" || true
  head -c 600 /tmp/_chat.json; echo
done
"""


def run_ssh(cmd: str, *, timeout: int = 300) -> tuple[str, int]:
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing in .env")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        HOST,
        username="root",
        password=PASSWORD,
        timeout=40,
        banner_timeout=120,
        auth_timeout=60,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        _, out, err = ssh.exec_command(cmd, timeout=timeout)
        text = (out.read() + err.read()).decode("utf-8", "replace")
        return text, out.channel.recv_exit_status()
    finally:
        ssh.close()


CONFIGURED_IDS = '"${REGENT_MODEL_NAME:-}" "${REGENT_MODEL_NAME_2:-}" "${REGENT_MODEL_NAME_3:-}"'
CANONICAL_IDS = "tencent/deepseek-v4-flash tencent/glm-5.2 tencent/deepseek-v4-pro"


def main() -> int:
    ids = CANONICAL_IDS if "--canonical" in sys.argv else CONFIGURED_IDS
    script = REMOTE_SCRIPT.replace("__MODEL_IDS__", ids)
    text, code = run_ssh(f"bash -s <<'EOS'\n{script}\nEOS")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
