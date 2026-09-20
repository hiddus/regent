"""P0-5 前置探查（第二轮）：定位 Postgres 凭据与可运行的 Python。"""
from __future__ import annotations

import sys

sys.path.insert(0, "deploy/novel")
from _ssh import Remote  # noqa: E402

r = Remote()

print("=== env file 中的数据库串 ===")
print(
    r.run(
        "grep -rn -i 'DATABASE_URL\\|POSTGRES' /opt/regent/.env 2>/dev/null | head -8"
    ).out[:1200]
)

print("=== postgres 容器环境 ===")
print(
    r.run(
        "docker exec regent-postgres env 2>/dev/null | grep -i 'POSTGRES\\|PGDATA' | head -8"
    ).out[:800]
)

print("=== api 容器内数据库串 ===")
print(
    r.run(
        "docker exec regent-api env 2>/dev/null | grep -i 'DATABASE_URL' | head -3"
    ).out[:800]
)

print("=== 项目 python ===")
print(
    r.run(
        "ls /opt/regent/current/core 2>/dev/null | head -5; "
        "docker exec regent-api python -c 'import regent, sys; print(sys.version)' 2>&1 | head -3"
    ).out[:600]
)
