"""缺陷 10：核验阶段两处字节级引文校验，把「省略号跳读」判成「捏造」。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


# ------------------------------------------------------------- 1. _is_grounded
sub(
    "_is_grounded",
    "def _quote_check(quotes: list[str], text: str) -> None:\n",
    "# 省略号：模型引长句时几乎必然跳读，跳读不等于捏造。\n"
    '_QUOTE_ELLIPSIS = re.compile(r"…|\\.{2,}|．{2,}")\n'
    "\n"
    "\n"
    "def _is_grounded(quote: str, text: str) -> bool:\n"
    '    """引文是否确有出处。\n'
    "\n"
    "    允许用省略号跳读——但**每一段被留下的片段都必须逐字存在**。省略号只是把若干\n"
    "    真实片段接起来，不是改写许可证：真机样本里 5 条「事实证据不在正文中」全部来自\n"
    "    ``A……B`` 这种跳读引用，模型给的是 ``passed=True``，是被字节级比对改判成了硬\n"
    "    失败，而硬失败又会连锁导致导演的 ACCEPT 被 Runtime 拒绝、整章判死。\n"
    "\n"
    "    没有省略号时仍按覆盖度判：见 ``_quote_check`` 的说明。\n"
    '    """\n'
    "    stripped = quote.strip()\n"
    "    if not stripped:\n"
    "        return False\n"
    "    if stripped in text:\n"
    "        return True\n"
    "    segments = [seg for seg in _QUOTE_ELLIPSIS.split(stripped) if seg.strip()]\n"
    "    if len(segments) > 1:\n"
    "        return all(seg.strip() in text for seg in segments)\n"
    "    run = _longest_common_run(stripped, text)\n"
    "    need = min(len(stripped), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(stripped))))\n"
    "    return run >= need\n"
    "\n"
    "\n"
    "def _quote_check(quotes: list[str], text: str) -> None:\n",
)

# --------------------------------------------------------- 2. _quote_check 复用
sub(
    "_quote_check body",
    '''        if stripped in text:
            continue
        run = _longest_common_run(stripped, text)
        need = min(len(stripped), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(stripped))))
        if run < need:
            raise ProductionStopped(
                f"导演判断缺少可核对的原文证据：引用「{stripped[:40]}」"
                f"与场上原文最长逐字重合 {run} 字，需要 {need} 字"
            )''',
    '''        if _is_grounded(stripped, text):
            continue
        run = _longest_common_run(stripped, text)
        need = min(len(stripped), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(stripped))))
        raise ProductionStopped(
            f"导演判断缺少可核对的原文证据：引用「{stripped[:40]}」"
            f"与场上原文最长逐字重合 {run} 字，需要 {need} 字；"
            "若用省略号跳读，每一段留下的片段都必须逐字存在"
        )''',
)

# ------------------------------------------------------------- 3. 事实证据
sub(
    "fact quote",
    '''        for fact in result.facts:
            if fact.quote not in take["content"]:
                issues.append("事实证据不在正文中")''',
    '''        for fact in result.facts:
            if not _is_grounded(fact.quote, take["content"]):
                issues.append("事实证据不在正文中")''',
)

# ------------------------------------------------------------- 4. 状态变化证据
sub(
    "state change quote",
    '''        if any(change.quote not in take["content"] for change in result.state_changes):
            issues.append("状态变化缺少正文证据")''',
    '''        if any(
            not _is_grounded(change.quote, take["content"])
            for change in result.state_changes
        ):
            issues.append("状态变化缺少正文证据")''',
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
