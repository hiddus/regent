"""Resume canaries with concrete product/ux swarm fixes after live_preview_qa passed.

Both goals have reachable styled previews; Delivery Role Swarm rejected them as
outline-only (delivery-product-outline / delivery-ux-surface). Tell the agent exactly
what to add, then poll.

Usage:
  python ops/resume_canaries_for_swarm_pass_2026_08_11.py [--minutes 30]
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
    "continue_fix：live_preview_qa 已通过。Delivery Role Swarm 因首页可见纯文本 "
    "<400 字符（delivery-product-outline / delivery-ux-surface）拒收。"
    "请在同一 Session 把 index.html 可见文案补到至少 450 字符（去标签后），并保持："
    "1) 主标题「Regent 恢复验证 2026-08-11」+ 当前日期；"
    "2) 「验证项」二级区块（编排链路/沙箱 bind/预览 QA/交付门禁）各写一两句说明；"
    "3) footer + 可感知交互（按钮或时间刷新）；"
    "4) styles.css 深色居中；"
    "5) 关完全部 todo 后再 submit。"
    "不要 Flask 后端；纯静态 HTML/CSS。可见文字不够会再次被 Swarm 拒。"
)

ANSWER_SCRIPT = r'''
import json, urllib.request, urllib.error
pid = __PID__
body = json.dumps({"message": __MSG__, "actor": "regent-ops:swarm-pass-resume"}).encode()
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
                   left(coalesce(metadata->'agent_loop_exit'->'ask_envelope'->>'gap_reasons','')::text, 180) AS reasons
            FROM goals WHERE id = CAST(:id AS uuid)
        """), {"id": gid}).mappings().first()
        rows.append({"goal": gid[:8], **(dict(g) if g else {})})
print(json.dumps(rows, ensure_ascii=False, default=str))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=30)
    args = parser.parse_args()
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
        for project_id, goal_id in TARGETS:
            script = ANSWER_SCRIPT.replace("__PID__", json.dumps(project_id)).replace(
                "__MSG__", json.dumps(ANSWER, ensure_ascii=False)
            )
            _, out, err = ssh.exec_command(
                "docker exec -i regent-api python - <<'PYEOF'\n" + script + "\nPYEOF",
                timeout=360,
            )
            print(f"ANSWER {goal_id[:8]}", out.read().decode("utf-8", "replace").strip()[-500:], flush=True)
            e = err.read().decode("utf-8", "replace").strip()
            if e:
                print("STDERR", e[:400], flush=True)

        poll = POLL.replace("__GIDS__", json.dumps([g for _, g in TARGETS]))
        deadline = time.time() + args.minutes * 60
        while time.time() < deadline:
            time.sleep(50)
            _, out, err = ssh.exec_command(
                "docker exec -i regent-api python - <<'PYEOF'\n" + poll + "\nPYEOF",
                timeout=150,
            )
            raw = out.read().decode("utf-8", "replace")
            line = raw.strip().splitlines()[-1] if raw.strip() else "[]"
            print("POLL", line, flush=True)
            try:
                rows = json.loads(line)
            except json.JSONDecodeError:
                continue
            if all(
                str(r.get("preview_ready")) == "true"
                or str(r.get("status")) in {"ACHIEVED", "FAILED", "EXHAUSTED"}
                for r in rows
            ):
                print("ALL REACHED TERMINAL")
                break
            # if both soft-paused again with same swarm reject, keep waiting until timeout;
            # human/ops may interrupt.
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
