"""Babysit durable multi-agent run: unstick only; respect Progress ROI gate.

Intervene when soft-paused / ASK_HUMAN / host unhealthy.
Do NOT empty continue_fix when ROI next_action is stop.
Prefer self_repair / replan_global messages from progress_roi metadata.
"""

from __future__ import annotations

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
STATE = ROOT / "docs" / "durable-multiagent-run-2026-08-13.json"

SELF_REPAIR_MSG = (
    "self_repair：Progress ROI 定向自修复。"
    "根因是首页可见字约 292<400，Swarm 判 outline/ux。"
    "只改 templates/index.html（及必要 static）：首页直接展示多段落产品说明+示例四段结果，"
    "去标签可见字必须明显超过 400；勿新建计划外路径；本轮写入必须缩小 blocking_gaps。"
)

REPLAN_MSG = (
    "replan_global：Progress ROI 要求全局重分析卡点。"
    "上一轮消耗无进步（首页可见字仍不足 / outline 未过）。"
    "请重规划工作清单：优先加厚首页可见面，禁止计划外路径与空转 resume；"
    "改完后 redeploy Preview 再验收。"
)

CONTINUE_MSG = SELF_REPAIR_MSG  # default substantive; never empty continue_fix


def load_ids() -> tuple[str, str]:
    data = json.loads(STATE.read_text(encoding="utf-8"))
    body = data.get("body") or {}
    return str(body["goal_id"]), str(body["project"]["id"])


def run_remote(ssh: paramiko.SSHClient, name: str, body: str, timeout: int = 300) -> str:
    remote = f"/tmp/{name}"
    sftp = ssh.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(body)
    sftp.close()
    _, out, err = ssh.exec_command(
        f"docker cp {remote} regent-api:{remote} && "
        f"docker exec -w /tmp -e PYTHONIOENCODING=utf-8 regent-api python {remote}",
        timeout=timeout,
    )
    return (out.read() + err.read()).decode("utf-8", "replace")


def poll_script(goal_id: str) -> str:
    return f"""
import json, urllib.request
from sqlalchemy import create_engine, text
from regent.config import get_settings
gid={json.dumps(goal_id)}
url=get_settings().database_url
sync=url if "+psycopg" in url else url.replace("postgresql://","postgresql+psycopg://",1)
eng=create_engine(sync)
with eng.connect() as c:
  g=c.execute(text('''
    SELECT status,
           metadata->>'execution_stage' AS stage,
           metadata->>'preview_ready' AS preview_ready,
           metadata->>'preview_url' AS preview_url,
           metadata->>'awaiting_human_intervention' AS awaiting,
           metadata->>'delivery_gap_kind' AS gap,
           metadata->'agent_loop_exit'->>'exit_kind' AS exit_kind,
           left(coalesce(metadata->'agent_loop_exit'->>'stop_reason',''),160) AS stop,
           left(coalesce(metadata->>'live_action',''),220) AS live_action,
           metadata->'progress_roi'->>'next_action' AS roi_next,
           metadata->'progress_roi'->>'verdict' AS roi_verdict,
           metadata->'progress_roi'->>'stagnant_streak' AS roi_streak,
           left(coalesce(metadata->'progress_roi'->>'summary',''),240) AS roi_summary,
           updated_at
    FROM goals WHERE id::text=:g
  '''), {{"g": gid}}).mappings().first()
  sess=c.execute(text('''
    SELECT status, epoch, version, updated_at
    FROM project_agent_sessions WHERE goal_id::text=:g
    ORDER BY updated_at DESC LIMIT 1
  '''), {{"g": gid}}).mappings().first()
out=dict(g or {{}})
out["session"]=dict(sess) if sess else None
try:
  health=json.loads(urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=10).read().decode())
  out["host_unhealthy"]=bool((health.get("host") or {{}}).get("unhealthy"))
  out["runs_running"]=health.get("runs_running")
  out["goals_active"]=health.get("goals_active")
except Exception as e:
  out["health_err"]=str(e)[:120]
preview=out.get("preview_url") or ""
if preview:
  try:
    path=preview if preview.startswith("http") else ("http://127.0.0.1:8000"+preview)
    if "118.31.171.159" in path:
      path=path.replace("http://118.31.171.159:8000","http://127.0.0.1:8000")
    r=urllib.request.urlopen(path, timeout=12)
    out["preview_http"]=r.status
    out["preview_chars"]=len(r.read())
  except Exception as e:
    out["preview_http"]=str(e)[:100]
print(json.dumps(out, ensure_ascii=False, default=str))
"""


def guide_script(project_id: str, message: str) -> str:
    return f"""
import json, urllib.request, urllib.error
pid={json.dumps(project_id)}
body=json.dumps({{"message": {json.dumps(message, ensure_ascii=False)}, "actor": "regent-ops:durable-babysit"}}).encode()
req=urllib.request.Request(
  f"http://127.0.0.1:8000/v1/app-projects/{{pid}}/guidance",
  data=body, headers={{"Content-Type":"application/json"}}, method="POST")
try:
  with urllib.request.urlopen(req, timeout=300) as resp:
    print(json.dumps({{"http": resp.status, "body": json.loads(resp.read().decode())}}, ensure_ascii=False, default=str))
except urllib.error.HTTPError as exc:
  print(json.dumps({{"http": exc.code, "error": exc.read().decode()[:800]}}, ensure_ascii=False))
"""


def needs_unstick(snap: dict) -> bool:
    stage = (snap.get("stage") or "").upper()
    awaiting = str(snap.get("awaiting") or "").lower() in {"1", "true", "yes"}
    exit_kind = (snap.get("exit_kind") or "").upper()
    sess = ((snap.get("session") or {}).get("status") or "").upper()
    if stage == "GENERATING" and not awaiting and sess == "ACTIVE":
        return False
    if awaiting:
        return True
    if stage in {
        "DELIVERY_SOFT_PAUSE",
        "PAUSED",
        "AWAITING_HUMAN",
        "PREVIEW_PRODUCT_QA_FAILED",
    }:
        return True
    if sess == "PAUSED" and exit_kind in {"ASK_HUMAN", "NEEDS_INPUT", "STOP"}:
        return True
    if snap.get("host_unhealthy"):
        return True
    return False


def guide_message_for_snap(snap: dict) -> tuple[str | None, str]:
    """Return (message_or_None, action_label). None message means do not resume."""
    next_action = str(snap.get("roi_next") or "").strip().lower()
    exit_kind = str(snap.get("exit_kind") or "").upper()
    stop = str(snap.get("stop") or "")
    if next_action == "stop" or "roi_no_progress" in stop or (
        exit_kind == "STOP" and "roi" in stop
    ):
        return None, "roi_stop_hold"
    if next_action == "replan_global":
        return REPLAN_MSG, "replan_global"
    if next_action == "self_repair":
        return SELF_REPAIR_MSG, "self_repair"
    # No ROI yet (pre-gate exits): still send substantive self_repair, never empty continue.
    return SELF_REPAIR_MSG, "self_repair_default"


def main() -> int:
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")
    goal_id, project_id = load_ids()
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
        raw = run_remote(ssh, "_durable_poll.py", poll_script(goal_id))
        print("POLL", raw.strip()[-1500:], flush=True)
        snap = json.loads(raw.strip().splitlines()[-1])
        log_path = ROOT / "docs" / "durable-multiagent-babysit-log-2026-08-13.jsonl"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "snap": snap}, ensure_ascii=False, default=str) + "\n")
        action = "let_run"
        if needs_unstick(snap):
            msg, action = guide_message_for_snap(snap)
            if msg is None:
                print("ROI_STOP_HOLD streak=", snap.get("roi_streak"), snap.get("roi_summary"), flush=True)
            else:
                print("UNSTICK action=", action, flush=True)
                guided = run_remote(
                    ssh,
                    "_durable_guide.py",
                    guide_script(project_id, msg),
                    timeout=360,
                )
                print("GUIDE", guided.strip()[-1200:], flush=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "ts": time.time(),
                                "action": action,
                                "roi_next": snap.get("roi_next"),
                                "guide": guided[-2000:],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        else:
            print(
                "LET_RUN stage=",
                snap.get("stage"),
                "session=",
                (snap.get("session") or {}).get("status"),
                "roi=",
                snap.get("roi_next"),
                flush=True,
            )
        status_path = ROOT / "docs" / "durable-multiagent-status-2026-08-13.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status = {}
        live = snap.get("live_action")
        if isinstance(live, str):
            try:
                live = json.loads(live)
            except Exception:
                pass
        status["last_poll_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        status["observed"] = {
            "goal_status": snap.get("status"),
            "stage": snap.get("stage"),
            "live": live,
            "preview_ready": snap.get("preview_ready"),
            "preview_url": snap.get("preview_url"),
            "preview_http": snap.get("preview_http"),
            "session": snap.get("session"),
            "host_unhealthy": snap.get("host_unhealthy"),
            "exit_kind": snap.get("exit_kind"),
            "gap": snap.get("gap"),
            "roi_next": snap.get("roi_next"),
            "roi_streak": snap.get("roi_streak"),
            "roi_verdict": snap.get("roi_verdict"),
            "action": action if needs_unstick(snap) else "let_run",
        }
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        ssh.close()


if __name__ == "__main__":
    raise SystemExit(main())
