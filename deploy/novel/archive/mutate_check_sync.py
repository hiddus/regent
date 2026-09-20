"""反证：部署期的「逐文件 sha256 核对 + 符号体检」必须能抓住 M2 真机 500 的那类错位。

命题：容器里只要有一个文件与本地树不一致（少拷、拷错、残留旧版），
**只核对不覆盖**的 `--verify-only` 必须报不一致并以非 0 退出。

为什么必须用 --verify-only：整树部署脚本本身会覆盖并修正错位，
拿「注入错位 → 跑整树部署」去测，测的是修复器而不是守卫（第一版就踩了这个坑）。

用法：python deploy/novel/mutate_check_sync.py
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ssh import Remote  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
SYNC = os.path.join(ROOT, "deploy", "novel", "sync_tree.py")
TARGET = "/app/core/src/regent/novel/application/executor.py"
BAK = "/tmp/executor.bak"
INJECT = (
    "docker exec regent-api sh -lc "
    f"\"printf '\\n# skew-injection\\n' >> {TARGET}\""
)


def sync(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, SYNC, *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def main() -> int:
    baseline = sync("--skip-frontend")
    if baseline.returncode != 0:
        print("[FAIL] 基线整树部署没过：")
        print(baseline.stdout[-1500:], baseline.stderr[-800:])
        return 2
    print("[baseline] 整树部署通过（清单全一致 + 符号就位）")

    if sync("--verify-only").returncode != 0:
        print("[FAIL] 干净状态下 --verify-only 就报错，反证无意义")
        return 2
    print("[baseline] --verify-only 通过（干净树不误报）")

    with Remote() as r:
        r.run(f"docker exec regent-api sh -lc \"cp {TARGET} {BAK}\"", check=True)
        r.run(INJECT, check=True)
        has = r.run(
            "docker exec regent-api python -c "
            "\"from regent.novel.application import executor as e;"
            " print('executor_version:', hasattr(e, 'executor_version'))\""
        ).text.strip()
        print(f"[injected] 容器 executor.py 已注入错位（符号仍在：{has}）")

    caught = sync("--verify-only")
    flagged = caught.returncode != 0 and "不一致" in caught.stdout
    print(f"[mutated] --verify-only rc={caught.returncode}（期望非 0）")
    for line in caught.stdout.splitlines():
        if "已核对" in line or "[FAIL]" in line or "bad:" in line:
            print("           ", line.strip()[:160])

    with Remote() as r:
        r.run(f"docker exec regent-api sh -lc \"cp {BAK} {TARGET}\"", check=True)
    restored = sync("--verify-only")
    ok = flagged and restored.returncode == 0
    print(f"[restored] 还原后 --verify-only rc={restored.returncode}（期望 0）")
    print("[PASS] 文件级错位会在重启前被拦下，且干净树不误报"
          if ok else "[FAIL] 反证未成立")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
