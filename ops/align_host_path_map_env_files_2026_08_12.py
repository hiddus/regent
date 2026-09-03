"""Align .deploy.env / .runtime.env HOST_PATH_MAP so future recreates don't roll back.

Usage:
  python ops/align_host_path_map_env_files_2026_08_12.py
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
WANT = "/var/lib/regent=/opt/regent;/opt/regent=/opt/regent"

REMOTE = r'''
from pathlib import Path
import re
WANT = "__WANT__"
for name in (".env", ".runtime.env", ".deploy.env"):
    p = Path("/opt/regent") / name
    if not p.is_file():
        print(name, "MISSING")
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^REGENT_HOST_PATH_MAP=.*$", text, flags=re.M):
        text2 = re.sub(r"^REGENT_HOST_PATH_MAP=.*$", f"REGENT_HOST_PATH_MAP={WANT}", text, flags=re.M)
        action = "patched" if text2 != text else "already"
    else:
        text2 = text.rstrip() + f"\nREGENT_HOST_PATH_MAP={WANT}\n"
        action = "appended"
    if text2 != text:
        p.write_text(text2, encoding="utf-8")
    print(name, action, [ln for ln in text2.splitlines() if "HOST_PATH_MAP" in ln][:1])
'''


def main() -> int:
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")
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
        script = REMOTE.replace("__WANT__", WANT)
        _, out, err = ssh.exec_command(f"python3 - <<'PY'\n{script}\nPY", timeout=60)
        print(out.read().decode("utf-8", "replace"))
        e = err.read().decode("utf-8", "replace").strip()
        if e:
            print("STDERR", e[:400])
        _, o2, _ = ssh.exec_command(
            "docker exec regent-api python -c \"from regent.config import get_settings; s=get_settings(); print(s.host_path_map); print(s.sandbox_mode); print(s.agent_sandbox_image)\"",
            timeout=60,
        )
        print("SETTINGS", o2.read().decode("utf-8", "replace"))
        _, o3, _ = ssh.exec_command(
            "curl -sf http://127.0.0.1:8000/v1/health | head -c 400; echo",
            timeout=60,
        )
        print("HEALTH", o3.read().decode("utf-8", "replace")[:500])
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
