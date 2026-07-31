"""Persist REGENT_DEPENDENCY_EGRESS_PROXY into /opt/regent/.deploy.env without recreate.

Live containers already inherit egress from prior Config.Env; this closes the
landmine where the next recreate would drop it because .deploy.env lacked the key.
If worker env is missing proxy, exits non-zero and asks for open_gq3_canary recreate.
"""

from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(Path(__file__).resolve().parents[1] / ".env").items()
}
PROXY = "http://regent-egress:3128"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    CFG.get("SERVER_IP") or "118.31.171.159",
    username=CFG.get("LOGIN_USER") or "root",
    password=CFG["LOGIN_PASSWORD"],
    timeout=30,
)
cmd = f"""
set -euo pipefail
ENVF=/opt/regent/.deploy.env
touch "$ENVF"
chmod 600 "$ENVF"
if grep -q '^REGENT_DEPENDENCY_EGRESS_PROXY=' "$ENVF"; then
  sed -i 's|^REGENT_DEPENDENCY_EGRESS_PROXY=.*|REGENT_DEPENDENCY_EGRESS_PROXY={PROXY}|' "$ENVF"
else
  echo 'REGENT_DEPENDENCY_EGRESS_PROXY={PROXY}' >> "$ENVF"
fi
grep '^REGENT_DEPENDENCY_EGRESS_PROXY=' "$ENVF"
echo '--- live ---'
docker exec regent-worker printenv REGENT_DEPENDENCY_EGRESS_PROXY
docker exec regent-api printenv REGENT_DEPENDENCY_EGRESS_PROXY
docker ps --filter name=regent-egress --format '{{{{.Names}}}} {{{{.Status}}}}'
"""
_, o, e = ssh.exec_command(cmd, timeout=60)
out = (o.read() + e.read()).decode("utf-8", "replace")
print(out)
code = e.channel.recv_exit_status() if hasattr(e, "channel") else 0
# paramiko: get exit from stdout channel
_, o2, e2 = ssh.exec_command(
    "docker exec regent-worker printenv REGENT_DEPENDENCY_EGRESS_PROXY", timeout=30
)
live = (o2.read() + e2.read()).decode().strip()
ssh.close()
if PROXY not in live:
    raise SystemExit(
        f"live worker missing egress ({live!r}); run ops/open_gq3_canary.py then "
        "ops/deploy_console.py"
    )
print("EGRESS_PERSISTED_OK")
