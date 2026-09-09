"""A-05 反证：把刚修好的地方逐个改回旧写法，确认新测试真的会失败。

每个 mutation 必须让指定的测试**失败**；出现 "STILL PASS" 说明该测试没有咬住问题。
脚本结束会把所有文件还原到运行前的内容。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/Scripts/python.exe"

W = ROOT / "core/src/regent/novel/application/works.py"
M = ROOT / "core/src/regent/novel/application/memory.py"
D = ROOT / "core/src/regent/novel/domain/memory.py"
G = ROOT / "core/src/regent/novel/application/generation.py"

SCOPED = "tests/unit/novel/test_path_change_scoped_memory.py"
CANARY = "tests/unit/novel/test_executor_canary.py"

MUTATIONS: list[tuple[str, Path, str, str, str]] = [
    (
        "M1 回到按类别整批失效",
        W,
        """    invalidated, conservative = await memory_app.invalidate_changed(
        session,
        work=work,
        changed_subjects=changed_subjects,
        reason="critical_path_changed",
        fallback_kinds=("promise", "character_arc"),
    )""",
        """    invalidated = await memory_app.invalidate_memory(
        session,
        work=work,
        reason="critical_path_changed",
        kinds=("promise", "character_arc"),
    )
    conservative = False""",
        SCOPED,
    ),
    (
        "M2 改动判定只看标题与顺序（漏掉承诺）",
        W,
        """            or prev.promise != node.promise
            or prev.node_type != node.node_type
""",
        "",
        SCOPED,
    ),
    (
        "M3 依赖图为空也敢给最小子图",
        M,
        """    if not edges:
        return domain.ReplayPlan(
            complete=False,
            reason="尚未建立依赖图：无法给出最小子图，应保守重做当前章后续场景",
        )
""",
        "",
        SCOPED,
    ),
    (
        "M4 按内部键拼解析（rule:主题）",
        D,
        """    explicit = {part for part in wanted if ":" in part}
    subjects = wanted - explicit
    keys: set[str] = set()
    for item in items:
        if item.key in explicit or item.subject in subjects:
            keys.add(item.key)
        elif subjects & set(item.entities):
            keys.add(item.key)
    return keys""",
        """    return {
        item_key("rule", part) if ":" not in part else part for part in wanted
    }""",
        SCOPED,
    ),
    (
        "M5 ASSEMBLE 重建上下文不带执行器身份",
        G,
        """        **executor_app.carry_over(run.generation_context),
""",
        "",
        CANARY,
    ),
    (
        "M6 在途运行的执行器延期切换不落记录",
        W,
        """    pinned = executor_app.pinned_executor(run)
    requested = executor_app.choose_executor(work_id)
    if requested != pinned:""",
        """    pinned = executor_app.pinned_executor(run)
    requested = executor_app.choose_executor(work_id)
    if False:""",
        CANARY,
    ),
]


def run_tests(target: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", target, "-q", "--no-header", "-x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-3:]
    return proc.returncode == 0, " | ".join(tail)


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8") for _n, p, _o, _x, _t in MUTATIONS}
    bad = 0
    try:
        for name, path, old, new, target in MUTATIONS:
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"[SKIP] {name}: 锚点命中 {text.count(old)} 次")
                bad += 1
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            passed, tail = run_tests(target)
            verdict = "STILL PASS（没咬住）" if passed else "FAILED（咬住了）"
            print(f"[{verdict}] {name}\n          {tail}")
            if passed:
                bad += 1
            path.write_text(originals[path], encoding="utf-8")
    finally:
        for path, content in originals.items():
            path.write_text(content, encoding="utf-8")
    print("\n=== 反证结束：%s ===" % ("全部咬住" if bad == 0 else f"{bad} 项没咬住"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
