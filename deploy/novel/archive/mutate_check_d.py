"""D-01～D-04 的离线反证：临时把修复退回旧行为，断言测试真的会红。

为什么要有这个文件：测试通过只能说明「实现没被测出问题」。把修复改回旧行为后
测试必须变红，否则测试是空壳——它只是在描述实现，没有咬住缺陷。

脚本把原文保存在内存里（不落 .bak），注入旧行为、跑测试、再**无论如何**恢复，
恢复后逐字节自检。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATION = ROOT / "core" / "src" / "regent" / "novel" / "application" / "generation.py"
WORKS = ROOT / "core" / "src" / "regent" / "novel" / "application" / "works.py"
DIRECTION = ROOT / "core" / "src" / "regent" / "novel" / "application" / "direction.py"
CONTEXT = ROOT / "core" / "src" / "regent" / "novel" / "domain" / "context.py"
TESTS = ["tests/unit/novel/test_d_batch.py"]

# 每处修复的回退：(文件, [(修复后的代码, 旧行为)])
D01_ASSEMBLE_DROP = (
    "core/src/regent/novel/application/generation.py",
    [
        # D-01：ASSEMBLE 重建不再保留纠错语义
        (
            "        **{\n"
            "            key: old_context[key]\n"
            '            for key in ("correction", "replay_reason", "corrections")\n'
            "            if key in old_context\n"
            "        },\n",
            "",
        ),
    ],
)
D01_MERGE_SILENT = (
    "core/src/regent/novel/application/works.py",
    [
        # D-01：不同报错退回「静默丢弃」（排队中一律拒绝合并）
        (
            "        if pending is None or not correction:\n"
            "            return False\n",
            "        if pending is None or not correction:\n"
            "            return False\n"
            "        return False\n",
        ),
    ],
)
D02_BARRIER_REMOVED = (
    "core/src/regent/novel/application/works.py",
    [
        # D-02：依赖屏障退回「起跑不再检查更早在途章」
        (
            "            if int(earlier_in_flight or 0) > 0:\n"
            "                continue\n",
            "            if False:\n"
            "                continue\n",
        ),
    ],
)
D03_PLAN_RAW = (
    "core/src/regent/novel/application/direction.py",
    [
        # D-03：导演计划请求退回「全量倾倒未投影 memory」
        (
            '            "context": {\n'
            "                k: v\n"
            "                for k, v in run.generation_context.items()\n"
            '                if k not in ("production", "memory")\n'
            "            },\n",
            '            "context": {k: v for k, v in run.generation_context.items()'
            ' if k != "production"},\n',
        ),
    ],
)
D03_CALLSITES = (
    "core/src/regent/novel/application/direction.py",
    [
        # D-03：ACT 不再带角色投影
        (
            '                memory=_memory_view(memory_payloads, "character", '
            'actor["persona"]),\n',
            "",
        ),
        # D-03：WATCH_TAKE 不再带导演投影
        (
            '            memory=_memory_view(memory_payloads, "director"),\n',
            "",
        ),
        # D-03：RENDER 不再带叙述者投影
        (
            '            memory=_memory_view(memory_payloads, "narrator"),\n',
            "",
        ),
        # D-03：WATCH_PROSE 不再带导演投影
        (
            '                "director_memory": _memory_view(memory_payloads, "director"),\n',
            "",
        ),
    ],
)
D03_CONTRACT = (
    "core/src/regent/novel/domain/context.py",
    [
        # D-03：装配器退回「忽略 memory 参数」
        ('        "character_memory": memory_items,\n', ""),
    ],
)
D04_EXHAUST_CHECK = (
    "core/src/regent/novel/application/generation.py",
    [
        # D-04：预算耗尽不再在调用前拒绝
        (
            '    if remaining <= 0:\n'
            '        raise ProductionStopped("终局判断预算已耗尽：不做终局判断")\n',
            "",
        ),
    ],
)
D04_WIRE = (
    "core/src/regent/novel/application/generation.py",
    [
        # D-04：预算上限退回「不传给 CallBroker」
        (
            '    broker = CallBroker(lease_owner=f"run:{run.id}", '
            "budget_limit_minor=remaining)",
            '    broker = CallBroker(lease_owner=f"run:{run.id}")',
        ),
    ],
)

SCENARIOS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "主场景（D-01装配/D-02屏障/D-03接线/D-04预算）": [
        D01_ASSEMBLE_DROP, D02_BARRIER_REMOVED, D03_PLAN_RAW, D03_CALLSITES,
        D03_CONTRACT, D04_EXHAUST_CHECK, D04_WIRE,
    ],
    "D-01 合并路径（排队时新报错被静默丢弃）": [D01_MERGE_SILENT],
}


def _run_tests() -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", *TESTS,
            "-p", "no:cacheprovider", "-q", "--no-header",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )


def _run_scenario(name: str, file_pairs: list[tuple[str, list[tuple[str, str]]]]) -> int:
    """注入一批回退 → 跑测试 → 恢复。返回变红例数（-1 表示注入失败）。

    同一文件的多条回退必须**先按文件聚合再依次应用**：各条目若独立从原文
    出发，后写的会覆盖先写的，前面的变异就悄悄失效（首跑即踩中此坑）。
    """
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
            print(f"!! 复原失败，以下文件与原文不一致，请手动检查：{dirty}")


def main() -> int:
    counts: dict[str, int] = {}
    try:
        for name, file_pairs in SCENARIOS.items():
            counts[name] = _run_scenario(name, file_pairs)
    finally:
        print(f"\n已恢复全部注入（{len(SCENARIOS)} 个场景）。")
    if any(v < 0 for v in counts.values()):
        return 2
    if any(v == 0 for v in counts.values()):
        bad = [k for k, v in counts.items() if v == 0]
        print(f"\n反证失败：这些场景退回旧行为后仍全绿——测试是空壳：{bad}")
        return 1
    print(
        "\n反证成立：每个场景退回旧行为后都有测试变红，"
        f"合计覆盖 {sum(counts.values())} 例次，说明测试咬住了修复。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
