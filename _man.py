from pathlib import Path
import json, hashlib, uuid, paramiko

c={}
for l in Path('.env').read_text(encoding='utf-8').splitlines():
    if '=' in l and not l.strip().startswith('#'):
        k,v=l.split('=',1); c[k]=v
cli=paramiko.SSHClient(); cli.set_missing_host_key_policy(paramiko.AutoAddPolicy()); cli.connect(c['SERVER_IP'],username=c['LOGIN_USER'],password=c['LOGIN_PASSWORD'],timeout=15)

run_id='32e2163a-0dc0-4233-a748-42b96a522ed3'
ws=f'/opt/regent/workspaces/{run_id}'
_,o,_=cli.exec_command(f'cat {ws}/.regent-manifest.json; echo ---; sha256sum {ws}/.regent-manifest.json {ws}/.regent-source.zip; python3 -c "import json,os; m=json.load(open(\'{ws}/.regent-manifest.json\')); print(len(m.get(\"files\",[])), sum(f.get(\"size\",0) for f in m.get(\"files\",[])))"', timeout=30)
print(o.read().decode(errors='replace'))

# Also check current outbox/gen state
sftp=cli.open_sftp();
with sftp.file('/tmp/_q.sql','w') as f:
    f.write(f"""
SELECT status, attempt, last_error FROM outbox_events WHERE id='0c39b948-8173-4ffb-97fc-35f65b1a7807';
SELECT status FROM generation_runs WHERE id='{run_id}';
SELECT id::text FROM workspace_snapshots WHERE generation_run_id='{run_id}';
""")
sftp.close()
_,o,_=cli.exec_command('docker cp /tmp/_q.sql regent-postgres:/tmp/_q.sql && docker exec regent-postgres psql -U regent -d regent -f /tmp/_q.sql', timeout=60)
print(o.read().decode(errors='replace'))
cli.close()
