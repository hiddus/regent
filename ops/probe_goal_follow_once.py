"""Follow-up: plan/run/app status for latest M6 goal."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
GID = "7ff58fc1-379a-4249-a499-1160b07c8294"
APP = "3d7fed19-c8f0-4874-8ea6-0ef2f4ba1e16"
RR = "ae4b28cf-07ab-49b6-89c8-e60d5e995336"

REMOTE = """
import json
from sqlalchemy import create_engine, text
from regent.config import get_settings
from regent.application.generation_strategy_policy import (
    resolve_effective_generation_strategy,
    stable_canary_bucket,
)
s = get_settings()
url = s.database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
gid = "GID"
app = "APP"
rr = "RR"
with eng.connect() as c:
    g = dict(c.execute(text("SELECT id::text, status, original_input, metadata, created_at FROM goals WHERE id=CAST(:g AS uuid)"), {"g": gid}).mappings().first())
    print("GOAL_STATUS", g["status"])
    print("ORIGINAL_INPUT", (g["original_input"] or "")[:500])
    print("METADATA", json.dumps(g["metadata"], ensure_ascii=False)[:800])
    print("BUCKET", stable_canary_bucket(gid), "RESOLVED", resolve_effective_generation_strategy(s, goal_id=gid))

    plans = list(c.execute(text(
        "SELECT id::text, created_at, contract_json->>'generator_ref' AS ref, "
        "contract_json->>'generation_strategy' AS strat "
        "FROM generation_plans WHERE requirement_revision_id=CAST(:rr AS uuid) ORDER BY created_at"
    ), {"rr": rr}).mappings())
    print("PLANS_BY_RR", len(plans), [dict(p) for p in plans])

    plans2 = list(c.execute(text(
        "SELECT p.id::text, p.created_at, p.contract_json->>'generator_ref' AS ref "
        "FROM generation_plans p JOIN requirement_revisions r ON r.id=p.requirement_revision_id "
        "WHERE r.goal_id=CAST(:g AS uuid) ORDER BY p.created_at"
    ), {"g": gid}).mappings())
    print("PLANS_BY_GOAL", len(plans2), [dict(p) for p in plans2])

    app_row = dict(c.execute(text("SELECT * FROM app_projects WHERE id=CAST(:a AS uuid)"), {"a": app}).mappings().first())
    print("APP", {k: (str(v)[:200] if v is not None else None) for k, v in app_row.items() if k in (
        "id","status","name","title","goal_id","created_at","updated_at","stage","delivery_state"
    ) or True})

    # hypotheses / decisions
    for t in ("hypotheses", "organization_versions", "goal_executions", "generation_jobs"):
        exists = c.execute(text("SELECT to_regclass(:t)"), {"t": "public." + t}).scalar()
        print("table", t, exists)

    msgs = list(c.execute(text(
        "SELECT id::text, role, left(coalesce(content, body, text, ''), 160), created_at "
        "FROM conversation_messages WHERE correlation_id=(SELECT correlation_id FROM goals WHERE id=CAST(:g AS uuid)) "
        "OR goal_id=CAST(:g AS uuid) ORDER BY created_at DESC LIMIT 8"
    ), {"g": gid}).mappings()) if False else []

    # discover conversation_messages columns
    cm_cols = [r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='conversation_messages' ORDER BY 1"
    ))]
    print("CM_COLS", cm_cols)
    # try flexible query
    q = "SELECT * FROM conversation_messages WHERE "
    if "goal_id" in cm_cols:
        q += "goal_id=CAST(:g AS uuid)"
        params = {"g": gid}
    elif "correlation_id" in cm_cols:
        q += "correlation_id=(SELECT correlation_id FROM goals WHERE id=CAST(:g AS uuid))"
        params = {"g": gid}
    else:
        q = None
        params = {}
    if q:
        rows = list(c.execute(text(q + " ORDER BY created_at DESC LIMIT 5"), params).mappings())
        print("MSGS", len(rows))
        for r in rows:
            d = dict(r)
            keep = {}
            for k, v in d.items():
                if v is None:
                    continue
                if k in ("id", "role", "status", "created_at", "goal_id") or "content" in k or "text" in k or "body" in k:
                    keep[k] = str(v)[:200]
            print(json.dumps(keep, ensure_ascii=False))

    # outbox cols + recent for goal
    ob_cols = [r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='outbox_events' ORDER BY 1"
    ))]
    print("OB_COLS", ob_cols)
    ts = "created_at" if "created_at" in ob_cols else ("available_at" if "available_at" in ob_cols else ob_cols[0])
    rows = list(c.execute(text(
        f"SELECT id::text, event_type, status, left(payload::text, 200) AS payload FROM outbox_events "
        f"WHERE payload::text LIKE :p ORDER BY {ts} DESC LIMIT 12"
    ), {"p": "%" + gid + "%"}))
    print("OUTBOX", len(rows))
    for r in rows:
        print(r)
"""


def main() -> None:
    body = (
        REMOTE.replace("GID", GID).replace("APP", APP).replace("RR", RR)
    )
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/_goal_follow.py", "w") as f:
        f.write(body)
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/_goal_follow.py regent-api:/tmp/_goal_follow.py && "
        "docker exec -w /app regent-api python /tmp/_goal_follow.py",
        timeout=60,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    ssh.close()


if __name__ == "__main__":
    main()
