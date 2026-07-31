"""Inspect where generator_ref lives in S0 schema."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(Path(__file__).resolve().parents[1] / ".env").items()
}
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    CFG.get("SERVER_IP") or "118.31.171.159",
    username=CFG.get("LOGIN_USER") or "root",
    password=CFG["LOGIN_PASSWORD"],
    timeout=30,
)
sql = r"""
from sqlalchemy import create_engine, text
from regent.config import get_settings
url = get_settings().database_url
sync = url
if sync.startswith('postgresql://'):
    sync = sync.replace('postgresql://', 'postgresql+psycopg://', 1)
eng = create_engine(sync)
with eng.connect() as c:
    for table in ('generation_plans', 'generation_runs', 'generation_plan_contracts'):
        cols = c.execute(text(
            "select column_name from information_schema.columns "
            "where table_name=:t order by ordinal_position"
        ), {'t': table}).scalars().all()
        print(table, cols)
    # search columns named generator_ref
    refs = c.execute(text(
        "select table_name, column_name from information_schema.columns "
        "where column_name in ('generator_ref','prompt_version') order by 1,2"
    )).all()
    print('ref_columns', refs)
    # sample plan row keys if jsonb
    row = c.execute(text('select * from generation_plans limit 1')).mappings().first()
    if row:
        print('sample_plan_keys', list(row.keys()))
        for k,v in row.items():
            if 'gen' in k.lower() or 'contract' in k.lower() or 'prompt' in k.lower() or 'meta' in k.lower():
                print(' ', k, type(v).__name__, repr(v)[:200])
"""
sftp = ssh.open_sftp()
with sftp.file("/tmp/gq3_schema.py", "w") as f:
    f.write(sql)
sftp.close()
_, o, e = ssh.exec_command(
    "docker cp /tmp/gq3_schema.py regent-api:/tmp/gq3_schema.py && "
    "docker exec -w /app regent-api python /tmp/gq3_schema.py",
    timeout=60,
)
print((o.read() + e.read()).decode("utf-8", "replace"))
ssh.close()
