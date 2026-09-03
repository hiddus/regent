"""End-to-end canary after the flash->pro switch and the code restore.

Everything below the surface now checks out (modules restored, provider tool
round-trip green on deepseek-v4-pro), but the queue is drained: 84 goals sit
ACTIVE with zero pending outbox events, so no amount of worker health proves the
delivery machine works. Submit one tiny goal and watch it move.

Usage:
  python ops/canary_after_model_switch_2026_08_11.py [--minutes 20]
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

IDEA = (
    "做一个极小的静态页面 canary：单页 index.html 显示标题"
    "「Regent 恢复验证 2026-08-11」和一行当前日期文本，配 styles.css 做居中深色样式。"
    "不需要后端、不需要接口。"
)

CREATE = r'''
import json, urllib.request

body = json.dumps({
    "idea": __IDEA__,
    "actor": "regent-ops:model-switch-canary",
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8000/v1/app-projects/drafts",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=180) as resp:
    data = json.loads(resp.read().decode())
print(json.dumps({
    "project_id": (data.get("project") or {}).get("id") or data.get("project_id"),
    "goal_id": data.get("goal_id"),
    "goal_status": data.get("goal_status"),
    "auto_started": data.get("auto_started"),
}, ensure_ascii=False, default=str))
'''

POLL = r'''
import json
from sqlalchemy import create_engine, text
from regent.config import get_settings

gid = __GID__
url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
with eng.connect() as c:
    g = c.execute(text("""
        SELECT status,
               metadata->>'execution_stage' AS stage,
               metadata->>'delivery_gap_kind' AS gap,
               metadata->>'preview_url' AS preview_url,
               metadata->>'preview_ready' AS preview_ready,
               metadata->'agent_loop_exit'->>'exit_kind' AS exit_kind,
               left(coalesce(metadata->'agent_loop_exit'->'result_bundle'->>'summary',''), 110) AS summary
        FROM goals WHERE id = CAST(:id AS uuid)
    """), {"id": gid}).mappings().first()
    runs = c.execute(text("""
        SELECT r.status, count(*) FROM runs r
        JOIN works w ON w.id = r.work_id
        WHERE w.goal_id = CAST(:id AS uuid) GROUP BY 1
    """), {"id": gid}).all()
    plans = c.execute(text("""
        SELECT status, count(*) FROM generation_plans
        WHERE created_at > now() - interval '30 minutes' GROUP BY 1
    """)).all()
    errs = c.execute(text("""
        SELECT left(coalesce(last_error,''), 110) AS err, count(*) FROM outbox_events
        WHERE aggregate_id = CAST(:id AS uuid) AND coalesce(last_error,'') <> ''
        GROUP BY 1 ORDER BY 2 DESC LIMIT 3
    """), {"id": gid}).all()
print(json.dumps({
    "goal": dict(g) if g else {},
    "runs": {str(k): v for k, v in runs},
    "recent_plans": {str(k): v for k, v in plans},
    "errors": [{"err": e[0], "n": e[1]} for e in errs],
}, ensure_ascii=False, default=str))
'''


def ssh_connect() -> paramiko.SSHClient:
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


def exec_in_api(ssh: paramiko.SSHClient, script: str, timeout: int = 240) -> str:
    _, out, err = ssh.exec_command(
        "docker exec -i regent-api python - <<'PYEOF'\n" + script + "\nPYEOF", timeout=timeout
    )
    text = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    if stderr.strip():
        print("STDERR", stderr.strip()[:400])
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=20)
    args = parser.parse_args()

    ssh = ssh_connect()
    try:
        raw = exec_in_api(ssh, CREATE.replace("__IDEA__", json.dumps(IDEA, ensure_ascii=False)))
        print("CREATE", raw.strip())
        created = json.loads(raw.strip().splitlines()[-1])
        gid = created.get("goal_id")
        if not gid:
            raise SystemExit(f"create failed: {created}")

        poll = POLL.replace("__GID__", json.dumps(gid))
        deadline = time.time() + args.minutes * 60
        while time.time() < deadline:
            time.sleep(45)
            raw = exec_in_api(ssh, poll, timeout=120)
            line = raw.strip().splitlines()[-1] if raw.strip() else "{}"
            print(f"[{int(time.time() % 100000)}] POLL", line, flush=True)
            try:
                state = json.loads(line)
            except json.JSONDecodeError:
                continue
            goal = state.get("goal") or {}
            if goal.get("preview_ready") == "true" or goal.get("exit_kind"):
                print("REACHED TERMINAL SIGNAL")
                break
            if str(goal.get("status")) in {"ACHIEVED", "FAILED", "EXHAUSTED", "WAITING_HUMAN"}:
                print("REACHED TERMINAL STATUS", goal.get("status"))
                break
        print("goal_id", gid)
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
