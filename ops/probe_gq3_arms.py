"""Arm attribution + traffic estimate on S0."""
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
sync = url if '+psycopg' in url else url.replace('postgresql://', 'postgresql+psycopg://', 1)
eng = create_engine(sync)
with eng.connect() as c:
    print('contract_generator_ref_all')
    for row in c.execute(text(
        "select contract_json->>'generator_ref' as ref, count(*) "
        "from generation_plans group by 1 order by 2 desc"
    )):
        print(' ', row[0], row[1])
    print('contract_generator_ref_since_gq3')
    # window opened ~ today; use last 1 day + last hour
    for row in c.execute(text(
        "select contract_json->>'generator_ref' as ref, count(*) "
        "from generation_plans where created_at > now() - interval '1 day' "
        "group by 1 order by 2 desc"
    )):
        print(' ', row[0], row[1])
    print('file_change_set_refs')
    for row in c.execute(text(
        "select generator_ref, count(*) from file_change_sets group by 1 order by 2 desc"
    )):
        print(' ', row[0], row[1])
    print('plans_per_day_14d')
    for row in c.execute(text(
        "select date_trunc('day', created_at)::date d, count(*) "
        "from generation_plans where created_at > now() - interval '14 days' "
        "group by 1 order by 1"
    )):
        print(' ', row[0], row[1])
    print('goals_per_day_14d')
    for row in c.execute(text(
        "select date_trunc('day', created_at)::date d, count(*) "
        "from goals where created_at > now() - interval '14 days' "
        "group by 1 order by 1"
    )):
        print(' ', row[0], row[1])
    print('run_status_14d')
    for row in c.execute(text(
        "select status, count(*) from generation_runs "
        "where created_at > now() - interval '14 days' group by 1 order by 2 desc"
    )):
        print(' ', row[0], row[1])
"""
sftp = ssh.open_sftp()
with sftp.file("/tmp/gq3_arms.py", "w") as f:
    f.write(sql)
sftp.close()
_, o, e = ssh.exec_command(
    "docker cp /tmp/gq3_arms.py regent-api:/tmp/gq3_arms.py && "
    "docker exec -w /app regent-api python /tmp/gq3_arms.py",
    timeout=60,
)
print((o.read() + e.read()).decode("utf-8", "replace"))
ssh.close()
