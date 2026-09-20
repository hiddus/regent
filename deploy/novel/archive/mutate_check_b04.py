"""B-04 的离线反证：临时把修复退回旧行为，断言测试真的会红。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "core" / "src" / "regent" / "novel" / "application" / "executor.py"
WORKS = ROOT / "core" / "src" / "regent" / "novel" / "application" / "works.py"
TESTS = ["tests/unit/novel/test_b04_dual_executor.py"]

SCENARIOS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "主场景（注册/钉桶/版本落上下文/重建保版本）": [
        (
            "core/src/regent/novel/application/executor.py",
            [
                # 退回：第二个真实策略不再注册
                (
                    "KNOWN_EXECUTORS = frozenset({ARCHITECTURE, LEGACY_EXECUTOR})",
                    "KNOWN_EXECUTORS = frozenset({ARCHITECTURE})",
                ),
                # 退回：executor_version 不再被钉住重建（ASSEMBLE 一重建就丢）
                (
                    'PINNED_CONTEXT_KEYS: tuple[str, ...] = ("executor", '
                    '"executor_version", DEFER_MARKER)',
                    'PINNED_CONTEXT_KEYS: tuple[str, ...] = ("executor", DEFER_MARKER)',
                ),
            ],
        ),
        (
            "core/src/regent/novel/application/works.py",
            [
                # 退回：重演硬编码回 director_v2（B-04 前的旧行为）——
                # 必须先于版本行删除注入，否则整块匹配不到
                (
                    "    executor = executor_app.choose_executor(work.id)\n"
                    "    context: dict = {\n"
                    '        "architecture_version": executor,\n'
                    '        "executor": executor,\n'
                    '        "executor_version": executor_app.executor_version(executor),\n'
                    "    }\n",
                    '    context: dict = {"architecture_version": ARCHITECTURE}\n',
                ),
                # 退回：start_run 不再写版本（12 空格缩进，ChapterRunModel 内）
                (
                    '            "executor_version": executor_app.executor_version(executor),\n',
                    "",
                ),
            ],
        ),
    ],
}


def _run_tests() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *TESTS, "-p", "no:cacheprovider", "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True,
    )


def _run_scenario(name: str, file_pairs: list[tuple[str, list[tuple[str, str]]]]) -> int:
    merged: dict[Path, list[tuple[str, str]]] = {}
    for rel, pairs in file_pairs:
        merged.setdefault(ROOT / rel, []).extend(pairs)
    originals: dict[Path, str] = {path: path.read_text(encoding="utf-8") for path in merged}
    try:
        missing: list[str] = []
        for path, pairs in merged.items():
            text = originals[path]
            for fixed, buggy in pairs:
                if fixed not in text:
                    missing.append(f"{path.name}: {fixed.strip()[:50]}")
                    continue
                text = text.replace(fixed, buggy, 1)
            path.write_text(text, encoding="utf-8")
        if missing:
            print(f"[{name}] 反证无法注入，以下代码不存在：{missing}")
            return -1
        result = _run_tests()
        failed = [
            line.strip().rsplit("::", 1)[-1]
            for line in result.stdout.splitlines()
            if line.strip().startswith("FAILED ")
        ]
        print(f"\n=== {name} ===")
        print("\n".join(sorted(set(failed))) if failed else "(无 FAILED 行)")
        print(f"变红例数：{len(failed)}")
        return len(failed)
    finally:
        dirty: list[str] = []
        for path, original in originals.items():
            path.write_text(original, encoding="utf-8")
            if path.read_text(encoding="utf-8") != original:
                dirty.append(str(path))
        if dirty:
            print(f"!! 复原失败，请手动检查：{dirty}")


def main() -> int:
    counts: dict[str, int] = {}
    try:
        for name, file_pairs in SCENARIOS.items():
            counts[name] = _run_scenario(name, file_pairs)
    finally:
        print(f"\n已恢复全部注入（{len(SCENARIOS)} 个场景）。")
    if any(v <= 0 for v in counts.values()):
        print("\n反证失败：退回旧行为后仍有场景全绿——测试是空壳。")
        return 1
    print(f"\n反证成立：合计 {sum(counts.values())} 例次变红，测试咬住了修复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
