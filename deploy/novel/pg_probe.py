"""在服务器上做 P0-5 前置探查：Postgres 凭据与项目路径。"""
from __future__ import annotations

import sys

sys.path.insert(0, "deploy/novel")
from _ssh import Remote  # noqa: E402

r = Remote()

print("=== compose postgres ===")
print(r.run("grep -n -A 14 postgres /opt/regent/compose.yaml | head -40").out[:1500])

print("=== db url in deploy ===")
print(
    r.run(
        "grep -rn DATABASE_URL /opt/regent/compose.yaml /opt/regent/deploy 2>/dev/null | head -8"
    ).out[:1200]
)

print("=== project venv ===")
print(r.run("ls /opt/regent/current 2>/dev/null | head; ls /opt/regent/.venv/bin/python* 2>/dev/null | head -3").out[:600])
