"""缺陷 7b：可引用文本漏了 rule_issues，导演引用规则冲突反被判捏造。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


sub(
    "evidence_text",
    '''        evidence_text = "\\n".join(
            [e["statement"] for e in take["events"]]
            + [line for a in take["performances"] for line in a["actions"] + a["dialogue"]]
        )
        _quote_check(result.evidence, evidence_text)''',
    '''        # rule_issues 必须可引用：提示词要求「有 rule_issues 必须重演」，而重演的
        # **理由**就是那条规则冲突本身。不把它放进可引用文本，等于要求导演拿一个
        # 不许引用的东西当证据——它只能把规则提示复述进 evidence，然后被判捏造。
        evidence_text = "\\n".join(
            [e["statement"] for e in take["events"]]
            + [line for a in take["performances"] for line in a["actions"] + a["dialogue"]]
            + [str(issue) for issue in take.get("rule_issues", [])]
        )
        _quote_check(result.evidence, evidence_text)''',
)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    for label, old, new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"[FAIL] 锚点 {label} 命中 {count} 次（期望 1）")
            return 1
        text = text.replace(old, new, 1)
    try:
        compile(text, str(TARGET), "exec")
    except SyntaxError as exc:
        print(f"[FAIL] 语法错误 {exc}")
        return 1
    TARGET.write_text(text, encoding="utf-8")
    print(f"[OK] {TARGET.name} 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
