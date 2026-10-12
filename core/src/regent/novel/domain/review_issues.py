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
from dataclasses import dataclass, field
from typing import Any, Literal

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


# --- 结构化议题协议（工作包1.1） ---

IssueStatus = Literal["OPEN", "EVIDENCE_DEFECT", "RESOLVED", "WITHDRAWN"]
IssueSeverity = Literal["hard", "soft"]
IssueOrigin = Literal["compile_self_review", "writer_self_review", "auditor", "reviewer", "program_check"]


@dataclass
class StructuredIssue:
    """统一议题结构（工作包1.1）。

    字段对齐计划：issue_id, code, severity, origin, origin_scene_id,
    affected_scene_ids, evidence_spans, source_hash, status, verify_rule。
    """

    issue_id: str
    code: str
    severity: IssueSeverity
    origin: IssueOrigin
    description: str
    origin_scene_id: str = ""
    affected_scene_ids: list[str] = field(default_factory=list)
    evidence_spans: list[str] = field(default_factory=list)
    source_hash: str = ""
    status: IssueStatus = "OPEN"
    verify_rule: str = ""
    resolved_version: str = ""
    resolved_evidence: str = ""

    def to_text(self) -> str:
        """转为文本格式（向后兼容字符串列表）。"""
        prefix = f"[{self.code}]" if self.code else ""
        return f"{prefix} {self.description}".strip()


def parse_issue_text(text: str, *, origin: IssueOrigin = "reviewer") -> StructuredIssue:
    """从文本解析议题（向后兼容）。"""
    import hashlib

    text = str(text or "").strip()
    code = ""
    match = re.match(r"^\[([^\]]+)\]\s*(.*)$", text, re.DOTALL)
    if match:
        code = match.group(1)
        description = match.group(2).strip()
    else:
        description = text
    severity: IssueSeverity = "soft" if any(text.startswith(p) for p in SOFT_ISSUE_PREFIXES) else "hard"
    return StructuredIssue(
        issue_id=hashlib.sha256(text.encode()).hexdigest()[:12],
        code=code,
        severity=severity,
        origin=origin,
        description=description,
        evidence_spans=[q.strip() for q in _QUOTE_RE.findall(text) if q.strip()],
    )


def is_hard_issue(issue: Any) -> bool:
    """单条议题是否为硬问题。

    硬问题会阻断正史；软问题只记录。递延的连续性债务（[gate_debt:cont]）代表真实
    时序/物件冲突，按硬处理，不再一概可 coerce。
    """
    text = str(issue or "").strip()
    if not text:
        return False
    if text.startswith(SOFT_ISSUE_PREFIXES):
        return False
    # 递延 prose_front：power_surface 为软，redundant（真复读）为硬。
    if text.startswith("[gate_debt:prose_front]"):
        return "redundant" in text
    # 其余（[front:title/cast/banned/redundant]、[pov:]、[prior_canon]、[editor:]、
    # [gate_debt:cont]、无结构化前缀的自然语言冲突）一律为硬。
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


def partition_hard_by_grounding(
    hard_issues: list[str], content: str
) -> tuple[list[str], list[str]]:
    """把硬问题拆成 (grounded, evidence_defect)。

    - evidence_defect：问题引用了「…」正文摘录，但摘录对不上当前正文——可能是误报
      （如把合法重读判成冲突），须走有界证据复核，不直接触发重写、不自动关闭。
    - grounded：其余硬问题。包含摘录命中正文的，以及不带摘录的确定性/结构性问题
      （前置硬门、字数不足、自然语言连续性冲突）——它们本就无需正文接地。
    """
    grounded: list[str] = []
    defect: list[str] = []
    for text in hard_issues:
        quotes = extract_quotes(text)
        if quotes and not any(quote_in_content(q, content) for q in quotes):
            defect.append(text)
        else:
            grounded.append(text)
    return grounded, defect


__all__ = [
    "SOFT_ISSUE_PREFIXES",
    "StructuredIssue",
    "extract_quotes",
    "is_hard_issue",
    "parse_issue_text",
    "partition_hard_by_grounding",
    "partition_issues",
    "quote_in_content",
]
