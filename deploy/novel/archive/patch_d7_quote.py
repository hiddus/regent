"""缺陷 7：导演 evidence 要求字节级逐字相同，一个代词还原就判死整章。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


# --------------------------------------------------------------- 1. 常量
sub(
    "quote constants",
    "MAX_NEW_PERSONAS_PER_CHAPTER = 2\n",
    "MAX_NEW_PERSONAS_PER_CHAPTER = 2\n"
    "# 引文核对：量**覆盖度**，不量同一性。\n"
    "# 真机样本：8 条 evidence 里 7 条逐字命中，第 8 条把原文的「他将开表器握在手中」\n"
    "# 写成「陈默将开表器握在手中」——把代词还原成人名——整章就判死了。要求字节级\n"
    "# 相同等于要求模型不能做任何正常的小改动，而这个检查的目的是「不许凭空捏造」，\n"
    "# 不是「不许改写」。凭空捏造的引文凑不出这么长的逐字重合，所以保证仍然成立。\n"
    "# 注意这与角色名归一**不是一回事**：角色名是身份键，猜错不可逆；引文只是\n"
    "# 审计用的出处，近义改写无害。风险不同，规则就该不同。\n"
    "QUOTE_MIN_RUN = 12\n"
    "QUOTE_MIN_RATIO = 0.6\n",
)

# --------------------------------------------------------- 2. _quote_check 重写
sub(
    "_quote_check",
    '''def _quote_check(quotes: list[str], text: str) -> None:
    if not quotes or any(not quote.strip() or quote not in text for quote in quotes):
        raise ProductionStopped("导演判断缺少可核对的原文证据")''',
    '''def _longest_common_run(quote: str, text: str) -> int:
    """两段文字的最长**连续**公共子串长度（只数逐字相同，不做模糊匹配）。"""
    if not quote or not text:
        return 0
    # 滚动数组 DP；两端都是千字量级，不必上后缀自动机。
    prev = [0] * (len(text) + 1)
    best = 0
    for char in quote:
        cur = [0] * (len(text) + 1)
        for j, other in enumerate(text, start=1):
            if char == other:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _quote_check(quotes: list[str], text: str) -> None:
    """核对导演的 evidence 确有出处。

    判据是**最长逐字重合**够长，而不是全串字节相同。阈值取「至少 12 字」与
    「引文本身的 60%」中较大者，再以引文长度为上限——短引文天然要求整串命中，
    长引文允许改掉几个字。
    """
    if not quotes:
        raise ProductionStopped("导演判断缺少可核对的原文证据")
    for quote in quotes:
        stripped = quote.strip()
        if not stripped:
            raise ProductionStopped("导演判断缺少可核对的原文证据：存在空白引用")
        if stripped in text:
            continue
        run = _longest_common_run(stripped, text)
        need = min(len(stripped), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(stripped))))
        if run < need:
            raise ProductionStopped(
                f"导演判断缺少可核对的原文证据：引用「{stripped[:40]}」"
                f"与场上原文最长逐字重合 {run} 字，需要 {need} 字"
            )''',
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
