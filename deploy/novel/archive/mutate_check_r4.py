"""R4 评估链入口的离线反证：退回「没有接线」的状态，测试必须变红。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "core" / "src" / "regent" / "novel" / "application" / "eval_runner.py"
TESTS = ["tests/unit/novel/test_eval_chain_entry.py"]

SCENARIOS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "主场景（采样不接真实运行 / 指标不接真实账本）": [
        (
            "core/src/regent/novel/application/eval_runner.py",
            [
                # 退回：采样永远为空——「库级能力存在但没有入口接真实数据」
                (
                    "    samples: list[domain.BlindSample] = []\n"
                    "    chapter_nos = sorted({no for no, _ in picked})\n",
                    "    samples: list[domain.BlindSample] = []\n"
                    "    chapter_nos: list[int] = []\n",
                ),
                # 退回：报告不接真实账本指标（成本/延迟/来源全空）
                (
                    "    metrics = await _arm_metrics(session, "
                    "samples=list(samples.values()), arms=arms)\n",
                    "    metrics = {'runs': {a: [] for a in arms}, 'cost': {}, "
                    "'cost_scene': {}, 'latency': {}, 'models': {}}\n",
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
            print(f"[{name}] 反证无法注入：{missing}")
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
        print("\n反证失败：退回后仍有场景全绿——测试是空壳。")
        return 1
    print(f"\n反证成立：合计 {sum(counts.values())} 例次变红，测试咬住了修复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
