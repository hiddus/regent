"""审校议题的严重性分级与证据接地（纯程序，两条链共用）。

设计要点（对齐 long-novel-repair-plan 工作包1.1）：
- 严重性与递延标签分离：真实物件/资源/时序冲突、一次性事件重演、场景越界为硬；
  文风建议、可接受省略为软。未分类的连续性问题不得自动降软。
- 审校硬问题必须带可程序核对的正文范围（当前正文逐字片段）。摘录不在当前正文、
  也对不上台账的硬问题按证据缺陷处理，不直接触发重写，也不自动关闭或降软。
- 接地允许安全的空白/标点规范化，但不抹去数字、否定等事实差异。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# 结构化顾问标签：只提示、不阻断正史。
SOFT_ISSUE_PREFIXES: tuple[str, ...] = (
    "[soft-cont:",
    "[editor-soft:",
    "[soft-outline]",
    "[commons:",
    "[front:power_surface",
)

# 摘录格式：审校被要求用「摘录「…」」给出可核对的正文范围。
_QUOTE_RE = re.compile(r"「(.+?)」", re.DOTALL)


def is_hard_issue(issue: Any) -> bool:
    """单条议题是否为硬问题。

    硬问题会阻断正史；软问题只记录。
    """
    text = str(issue or "").strip()
    if not text:
        return False
    if text.startswith(SOFT_ISSUE_PREFIXES):
        return False
    # 其余（[front:title/cast/banned/redundant]、[pov:]、[prior_canon]、[editor:]、
    # 无结构化前缀的自然语言冲突）一律为硬。
    return True


def partition_issues(issues: list[Any] | None) -> tuple[list[str], list[str]]:
    """把议题拆成 (hard, soft)，保持原顺序、去重。"""
    hard: list[str] = []
    soft: list[str] = []
    seen: set[str] = set()
    for raw in issues or []:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        (hard if is_hard_issue(text) else soft).append(text)
    return hard, soft


def _normalize_for_grounding(text: str) -> str:
    """安全规范化：去掉空白与标点，保留汉字/字母/数字。

    只用于接地比对——数字、否定词等事实差异体现在汉字/数字上，不会被抹去；
    标点与空白的差异（换行、全半角、句读）不影响是否命中正文。
    """
    text = unicodedata.normalize("NFKC", str(text or ""))
    kept = []
    for ch in text:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("Z"):
            continue
        kept.append(ch)
    return "".join(kept)


def extract_quotes(issue: Any) -> list[str]:
    """从议题文本中抽取「…」内的正文摘录候选。"""
    return [q.strip() for q in _QUOTE_RE.findall(str(issue or "")) if q.strip()]


def quote_in_content(quote: str, content: str) -> bool:
    """复核给出的逐字片段是否真的命中当前正文（安全规范化后）。"""
    q = _normalize_for_grounding(quote)
    if not q:
        return False
    return q in _normalize_for_grounding(content)


# 程序自己产生的硬问题不要求模型摘录。审校写出的硬问题必须带「…」正文范围。
_PROGRAM_ISSUE_PREFIXES: tuple[str, ...] = (
    "整章正文不足",
    "节点完成缺少",
    "[front:",
    "[pov:",
    "[semantic]",
    "boundary_",
    "ledger_",
    "repetition:",
    "constraint_",
)


def _program_authored(text: str) -> bool:
    return text.startswith(_PROGRAM_ISSUE_PREFIXES)


def partition_hard_by_grounding(
    hard_issues: list[str], content: str
) -> tuple[list[str], list[str]]:
    """把硬问题拆成 (grounded, evidence_defect)。

    - evidence_defect：摘录对不上当前正文，或审校硬问题没有可核对摘录。
      须走有界证据复核，不直接触发重写、不自动关闭。
    - grounded：摘录命中正文，或程序自己产生的确定性硬问题。
    """
    grounded: list[str] = []
    defect: list[str] = []
    for text in hard_issues:
        quotes = extract_quotes(text)
        if quotes and not any(quote_in_content(q, content) for q in quotes):
            defect.append(text)
        elif not quotes and not _program_authored(text):
            defect.append(text)
        else:
            grounded.append(text)
    return grounded, defect


__all__ = [
    "SOFT_ISSUE_PREFIXES",
    "extract_quotes",
    "is_hard_issue",
    "partition_hard_by_grounding",
    "partition_issues",
    "quote_in_content",
]
