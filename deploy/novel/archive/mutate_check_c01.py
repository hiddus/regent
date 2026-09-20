"""C-01～C-05 的离线反证：临时把修复退回旧行为，断言测试真的会红。

为什么要有这个文件：测试通过只能说明「实现没被测出问题」。把修复改回旧行为后
测试必须变红，否则测试是空壳——它只是在描述实现，没有咬住缺陷。

脚本会备份 works.py、注入旧行为、跑测试、再**无论如何**恢复原文件。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKS = ROOT / "core" / "src" / "regent" / "novel" / "application" / "works.py"
BACKUP = ROOT / "deploy" / "novel" / ".works_mutate_backup.py"
TESTS = [
    "tests/unit/novel/test_c_batch.py",
    "tests/unit/novel/test_c01_resume_api.py",
]

# (修复后的代码, 退回的旧行为)
REVERTS: list[tuple[str, str]] = [
    # C-01：纠错内容不再写入 generation_context
    ('    if correction:\n', '    if False:\n'),
    # C-01：不再拦截重复排队
    ('    if int(pending_replay or 0) > 0:\n', '    if False:\n'),
    # C-01：不再按作品状态区分话术，一律声称会自动重演
    ('    if state == StoryWorkState.DONE.value:\n', '    if False:\n'),
    # C-02：不再用用户终局约束拦扩卷
    (
        '    intent = await _ending_intent_of(work)\n'
        '    if int(intent.target_volume_count) > 0:\n',
        '    intent = await _ending_intent_of(work)\n'
        '    if False:\n',
    ),
    # C-02：80% 完成度重新触发扩卷
    ('    if not last_node_done:\n', '    if False:\n'),
    # C-01：恢复入口不再要求「确有排队中的重演」，可把作品空放回 RUNNING
    ('    if int(pending or 0) <= 0:\n', '    if int(pending or 0) < 0:\n'),
    # C-01：恢复入口不再接受 DONE，完结作品纠错后无法真正执行重演
    (
        '    if state not in (\n'
        '        StoryWorkState.DONE.value,\n'
        '        StoryWorkState.PAUSED_COST.value,\n',
        '    if state not in (\n'
        '        StoryWorkState.PAUSED_COST.value,\n',
    ),
]

# 每处缺陷的回退写法：(文件, [(修复后, 旧行为)])
C03_LOOSE = (
    "core/src/regent/novel/domain/memory.py",
    [
        # C-03：退回「按 subject 兑现」，会把同一个人的多条承诺一起关掉
        (
            '        hit = item.key in targets or str(item.content or "").strip() in targets\n'
            '        if not hit and item.subject in targets:\n'
            '            hit = open_counts.get(item.subject, 0) == 1\n'
            '        if hit:\n',
            '        hit = item.subject in targets or item.key in targets\n'
            '        if hit:\n',
        ),
    ],
)
# C-04 的两个方向**互斥**，不能同批注入：
# 放宽（无证据也算独立）会让「已声明独立」的正向测试照样通过；
# 收紧（声明不被读取）会让「无交集不算独立」的反向测试照样通过。
C04_LOOSE = (
    "core/src/regent/novel/application/memory.py",
    [
        # 实体无交集重新被自动认证为 independent
        (
            '        if not attached and item.declared_independent:\n',
            '        if not attached:\n',
        ),
    ],
)
C04_STRICT = (
    "core/src/regent/novel/domain/memory.py",
    [
        # 创作输入里的「显式独立」声明不再被读取，独立永远无从认证
        (
            '            declared_independent=bool(fact.get("independent")),',
            '            declared_independent=False,',
        ),
    ],
)
C05_DROP = (
    "core/src/regent/novel/application/generation.py",
    [
        # C-05：终局请求不再携带已写正文 / 已核验事实 / 未兑现承诺
        (
            '        "accepted_text": accepted_text[-4000:],\n'
            '        "verified_facts": [\n'
            '            str(f.get("statement", "")) for f in verified_facts\n'
            '        ][-40:],\n'
            '        "open_promises": '
            '[str(p.get("content", "")) for p in open_promises][-20:],\n',
            "",
        ),
    ],
)

# C-01 生产入口级缺陷：naive/aware 时间比较 → 鉴权路径 500 而不是 401
C01_NAIVE_DT = (
    "core/src/regent/novel/application/principal.py",
    [
        (
            "    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)",
            "    return value",
        ),
    ],
)
# C-01 生产入口级缺陷：事件序列分配退回裸 SQL，UUID 绑不上非 Postgres 驱动
C01_RAW_SQL = (
    "core/src/regent/novel/application/events.py",
    [
        (
            "    dialect_insert = sqlite_insert if dialect_name == \"sqlite\" else pg_insert",
            "    dialect_insert = pg_insert",
        ),
    ],
)

# 场景 = 一批同时注入的回退。同一缺陷的相反方向必须分在不同场景，
# 否则一个方向会把另一个方向掩盖掉，反证看起来成立实则漏测。
SCENARIOS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "主场景（C-01/C-02/C-03/C-04放宽/C-05）": [
        C03_LOOSE, C04_LOOSE, C05_DROP, C01_NAIVE_DT, C01_RAW_SQL,
    ],
    "C-04 收紧（声明不被读取）": [C04_STRICT],
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
    """注入一批回退 → 跑测试 → 恢复。返回变红例数（-1 表示注入失败）。"""
    targets = [(WORKS, REVERTS)]
    targets += [(ROOT / rel, pairs) for rel, pairs in file_pairs]
    # 原文保存在内存里，不落 .bak：备份文件一旦残留或被别的场景覆盖，
    # 反证脚本自己就会把工作树改坏（已发生过一次，变异态被留在 domain/memory.py）。
    originals: dict[Path, str] = {path: path.read_text(encoding="utf-8") for path, _ in targets}
    try:
        missing: list[str] = []
        for path, pairs in targets:
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
        # 短摘要会被 tail 截断，必须自己数，否则「变红几例」不可核对。
        print(f"\n=== {name} ===")
        print("\n".join(failed) if failed else "(无 FAILED 行)")
        print(f"变红例数：{len(failed)}")
        return len(failed)
    finally:
        dirty: list[str] = []
        for path, original in originals.items():
            path.write_text(original, encoding="utf-8")
            # 复原后自检：写回的内容必须与原文逐字节一致，否则脚本有 bug。
            if path.read_text(encoding="utf-8") != original:
                dirty.append(str(path))
        if dirty:
            print(f"!! 复原失败，以下文件与原文不一致，请手动检查：{dirty}")
        stray = sorted(p.name for p in WORKS.parent.rglob("*.bak"))
        if stray:
            print(f"!! 发现残留备份文件：{stray}")


def main() -> int:
    """把每个修复退回旧行为，跑测试，再**无论如何**恢复全部文件。"""
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
