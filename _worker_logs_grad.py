from pathlib import Path
import json
import paramiko

env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.split('=',1); env[k]=v
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(env['SERVER_IP'], username=env['LOGIN_USER'], password=env['LOGIN_PASSWORD'], timeout=15)
_,o,e=c.exec_command(
  "docker logs regent-worker --since 40m 2>&1 | "
  "grep -E '6dc62bcb|2c3a3e77|research_more|discovery|ERROR|Traceback|ExternalOperation|PREVIEW' | tail -80"
)
print(o.read().decode()[-6000:])
print(e.read().decode()[-1000:])
c.close()
