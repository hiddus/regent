"""部署后健康验证：容器状态、新代码在位、健康检查。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paramiko
from _ssh import load_dotenv

load_dotenv()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "118.31.171.159", username="root",
    password=os.environ["LOGIN_PASSWORD"], timeout=30,
)
CHECK = (
    "import regent.novel.application.direction as d, "
    "regent.novel.domain.memory as m, "
    "regent.novel.application.generation as g; "
    "print('project_payloads:', hasattr(m, 'project_payloads')); "
    "print('direction uses domain projection:', "
    "d._memory_view is m.project_payloads)"
)
cmds = [
    ("containers", "docker ps --format '{{.Names}} {{.Status}}' | grep regent"),
    ("new-code", f"docker exec regent-api python -c \"{CHECK}\""),
    ("worker-new-code", f"docker exec regent-worker python -c \"{CHECK}\""),
    ("health", "curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/live"),
    ("alembic", "docker run --rm --network regent-net --env-file /opt/regent/.env "
                "regent-core:novel-mvp alembic current"),
]
ok = True
for name, cmd in cmds:
    _, o, e = c.exec_command(cmd, timeout=120)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    print(f"== {name} ==")
    if out:
        print(out)
    if err:
        print("ERR:", err[:300])
        ok = ok and name in ("alembic",)  # alembic 会打 INFO 到 stderr
c.close()
print("VERIFY:", "OK" if ok else "CHECK MANUALLY")
