"""Answer both paused canaries and watch whether delivery now completes.

Both goals stopped at PROGRESS_LOOP/ASK_HUMAN only because the sandbox bind was broken
(fixed by ops/fix_agent_sandbox_bind_2026_08_11.py). Answer via the console path
(POST /v1/app-projects/{id}/guidance -> resume_after_human) so the same Agent Session
continues, then poll until preview_ready or a fresh terminal signal.

Usage:
  python ops/resume_canaries_after_sandbox_fix_2026_08_11.py [--minutes 25]
"""

from __future__ import annotations

import argparse
import json
import time
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

TARGETS = [
    ("c3af7c4e-74f7-46b8-bf1d-fd2977940b09", "7666ab1c-28ce-4cbf-85c1-d2d17ffeef29"),
    ("9d841af7-73fe-45de-8e05-e3b08c07c888", "c99aa66d-c2be-4bf7-a787-fb43635c4821"),
]
ANSWER = (
    "continue_fix：沙箱已修复，run_command 现在可以正常执行。"
    "请在同一 Session 继续完成剩余清单项，跑一次命令自验后再交付。"
)

ANSWER_SCRIPT = r'''
import json, urllib.request, urllib.error

pid = __PID__
body = json.dumps({"message": __MSG__, "actor": "regent-ops:sandbox-fix-resume"}).encode()
req = urllib.request.Request(
    f"http://127.0.0.1:8000/v1/app-projects/{pid}/guidance",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=300) as resp:
        print(json.dumps({"http": resp.status, "body": json.loads(resp.read().decode())}, ensure_ascii=False, default=str))
except urllib.error.HTTPError as exc:
    print(json.dumps({"http": exc.code, "error": exc.read().decode()[:500]}, ensure_ascii=False))
'''

POLL = r'''
import json
from sqlalchemy import create_engine, text
from regent.config import get_settings

gids = __GIDS__
url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
rows = []
with eng.connect() as c:
    for gid in gids:
        g = c.execute(text("""
            SELECT status,
                   metadata->>'execution_stage' AS stage,
                   metadata->>'delivery_gap_kind' AS gap,
                   metadata->>'preview_url' AS preview_url,
                   metadata->>'preview_ready' AS preview_ready,
                   metadata->'agent_loop_exit'->>'exit_kind' AS exit_kind,
                   metadata->'agent_loop_exit'->>'stop_reason' AS stop_reason,
                   left(coalesce(metadata->'agent_loop_exit'->'result_bundle'->>'summary',''), 90) AS summary
            FROM goals WHERE id = CAST(:id AS uuid)
        """), {"id": gid}).mappings().first()
        t = c.execute(text("""
            SELECT count(*) FROM agent_transcripts WHERE created_at > now() - interval '30 minutes'
        """)).scalar()
        rows.append({"goal": gid[:8], **(dict(g) if g else {}), "fresh_transcripts": t})
print(json.dumps(rows, ensure_ascii=False, default=str))
'''


def connect() -> paramiko.SSHClient:
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
    return ssh


def exec_api(ssh: paramiko.SSHClient, script: str, timeout: int = 360) -> str:
    _, out, err = ssh.exec_command(
        "docker exec -i regent-api python - <<'PYEOF'\n" + script + "\nPYEOF", timeout=timeout
    )
    body = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace").strip()
    if e:
        print("STDERR", e[:600], flush=True)
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=25)
    args = parser.parse_args()

    ssh = connect()
    try:
        for project_id, goal_id in TARGETS:
            script = ANSWER_SCRIPT.replace("__PID__", json.dumps(project_id)).replace(
                "__MSG__", json.dumps(ANSWER, ensure_ascii=False)
            )
            raw = exec_api(ssh, script)
            print(f"ANSWER {goal_id[:8]} {raw.strip()[-600:]}", flush=True)

        poll = POLL.replace("__GIDS__", json.dumps([g for _, g in TARGETS]))
        deadline = time.time() + args.minutes * 60
        while time.time() < deadline:
            time.sleep(45)
            raw = exec_api(ssh, poll, timeout=150)
            line = raw.strip().splitlines()[-1] if raw.strip() else "[]"
            print("POLL", line, flush=True)
            try:
                rows = json.loads(line)
            except json.JSONDecodeError:
                continue
            done = [
                r
                for r in rows
                if str(r.get("preview_ready")) == "true"
                or str(r.get("status")) in {"ACHIEVED", "FAILED", "EXHAUSTED"}
            ]
            if len(done) == len(rows):
                print("ALL REACHED TERMINAL")
                break
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
