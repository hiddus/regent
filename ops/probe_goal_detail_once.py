"""Detail dump for one goal id (default: latest M6 candidate)."""
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

REMOTE = """
import json
from sqlalchemy import create_engine, text
from regent.config import get_settings
s = get_settings()
url = s.database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
gid = "GID_PLACEHOLDER"
with eng.connect() as c:
    cols = [r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='goals' ORDER BY 1"
    ))]
    print("COLS", cols)
    row = dict(c.execute(text("SELECT * FROM goals WHERE id=CAST(:g AS uuid)"), {"g": gid}).mappings().first())
    for k, v in row.items():
        if v is None:
            continue
        print(k, ":", repr(v)[:400])
    rr_cols = [r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='requirement_revisions' ORDER BY 1"
    ))]
    print("RR_COLS", rr_cols)
    rrs = list(c.execute(text(
        "SELECT * FROM requirement_revisions WHERE goal_id=CAST(:g AS uuid) ORDER BY created_at"
    ), {"g": gid}).mappings())
    print("RR_N", len(rrs))
    for rr in rrs:
        d = {k: (str(v)[:200] if v is not None else None) for k, v in dict(rr).items()}
        print(json.dumps(d, ensure_ascii=False, default=str))
    for t in (
        "conversation_messages", "messages", "goal_messages", "chat_messages",
        "outbox_events", "app_projects",
    ):
        exists = c.execute(text("SELECT to_regclass(:t)"), {"t": "public." + t}).scalar()
        print("table", t, exists)
    # recent outbox mentioning goal
    if c.execute(text("SELECT to_regclass('public.outbox_events')")).scalar():
        rows = list(c.execute(text(
            "SELECT id::text, event_type, status, created_at, left(payload::text, 180) "
            "FROM outbox_events WHERE payload::text LIKE :p ORDER BY created_at DESC LIMIT 15"
        ), {"p": "%" + gid + "%"}))
        print("OUTBOX_N", len(rows))
        for r in rows:
            print(r)
"""


def main() -> None:
    body = REMOTE.replace("GID_PLACEHOLDER", GID)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        CFG.get("SERVER_IP") or "118.31.171.159",
        username=CFG.get("LOGIN_USER") or "root",
        password=CFG["LOGIN_PASSWORD"],
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/_goal_detail.py", "w") as f:
        f.write(body)
    sftp.close()
    _, o, e = ssh.exec_command(
        "docker cp /tmp/_goal_detail.py regent-api:/tmp/_goal_detail.py && "
        "docker exec -w /app regent-api python /tmp/_goal_detail.py",
        timeout=60,
    )
    print((o.read() + e.read()).decode("utf-8", "replace"))
    ssh.close()


if __name__ == "__main__":
    main()
