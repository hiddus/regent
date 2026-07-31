"""Check whether agentic plans ever produce file_change_sets / completed runs."""
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
    print('plans_to_runs_by_ref')
    q = '''
    select p.contract_json->>\'generator_ref\' as ref,
           r.status,
           count(*)
    from generation_plans p
    join generation_runs r on r.plan_id = p.id
    group by 1,2
    order by 1,3 desc
    '''
    for row in c.execute(text(q)):
        print(' ', row[0], row[1], row[2])
    print('agentic_plans_with_changeset')
    q2 = '''
    select count(*) from generation_plans p
    join generation_runs r on r.plan_id = p.id
    join file_change_sets f on f.generation_run_id = r.id
    where p.contract_json->>\'generator_ref\' = \'agentic-generation-v1\'
    '''
    print(' ', c.execute(text(q2)).scalar())
    print('recent_agentic_failures')
    q3 = '''
    select r.failure_code, count(*)
    from generation_plans p
    join generation_runs r on r.plan_id = p.id
    where p.contract_json->>\'generator_ref\' = \'agentic-generation-v1\'
      and r.created_at > now() - interval \'3 days\'
    group by 1 order by 2 desc
    '''
    for row in c.execute(text(q3)):
        print(' ', row[0], row[1])
    print('egress_in_worker_settings')
    from regent.config import get_settings as gs
    s = gs()
    print(' ', repr(s.dependency_egress_proxy), s.generation_strategy_canary_percent, s.generation_strategy)
"""
# Fix escaping - use normal quotes in remote file
sql = """
from sqlalchemy import create_engine, text
from regent.config import get_settings
url = get_settings().database_url
sync = url if '+psycopg' in url else url.replace('postgresql://', 'postgresql+psycopg://', 1)
eng = create_engine(sync)
with eng.connect() as c:
    print('plans_to_runs_by_ref')
    q = text('''
    select p.contract_json->>'generator_ref' as ref,
           r.status,
           count(*)
    from generation_plans p
    join generation_runs r on r.plan_id = p.id
    group by 1,2
    order by 1,3 desc
    ''')
    for row in c.execute(q):
        print(' ', row[0], row[1], row[2])
    print('agentic_plans_with_changeset', c.execute(text('''
    select count(*) from generation_plans p
    join generation_runs r on r.plan_id = p.id
    join file_change_sets f on f.generation_run_id = r.id
    where p.contract_json->>'generator_ref' = 'agentic-generation-v1'
    ''')).scalar())
    print('recent_agentic_run_status')
    for row in c.execute(text('''
    select r.status, r.failure_code, count(*)
    from generation_plans p
    join generation_runs r on r.plan_id = p.id
    where p.contract_json->>'generator_ref' = 'agentic-generation-v1'
      and r.created_at > now() - interval '3 days'
    group by 1,2 order by 3 desc
    ''')):
        print(' ', row[0], row[1], row[2])
    s = get_settings()
    print('settings', repr(s.dependency_egress_proxy), s.generation_strategy_canary_percent, s.generation_strategy_canary_gate)
"""
sftp = ssh.open_sftp()
with sftp.file("/tmp/gq3_bias.py", "w") as f:
    f.write(sql)
sftp.close()
_, o, e = ssh.exec_command(
    "docker cp /tmp/gq3_bias.py regent-api:/tmp/gq3_bias.py && "
    "docker exec -w /app regent-api python /tmp/gq3_bias.py",
    timeout=60,
)
print((o.read() + e.read()).decode("utf-8", "replace"))
ssh.close()
