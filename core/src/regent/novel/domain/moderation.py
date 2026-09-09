"""内容审核的规则扫描（FR-25 / G-23）。

**这是疑似命中提名，不是审核结论。**

- 命中只产生待处理的 `ModerationCase`（PENDING），不阻断发布、不删除内容。
- 词表由运营/合规侧配置（环境变量），代码只提供机制；词表为空时是
  “无法扫描”，不是“扫描通过”——两者必须可区分（G-23：无结论不得视为通过）。
- 本模块不替代第三方内容审核服务，也不构成任何合规判定。
"""

# Chinese docstrings deliberately use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import os
from dataclasses import dataclass

TERMS_ENV = "NOVEL_MODERATION_TERMS"


@dataclass(frozen=True)
class Hit:
    """一次疑似命中。offset 用于在正文中定位，不存储正文片段本身。"""

    term: str
    offset: int


@dataclass(frozen=True)
class ScanResult:
    configured: bool
    hits: tuple[Hit, ...]

    @property
    def scanned(self) -> bool:
        """只有配置了词表才算真正扫描过。"""
        return self.configured

    @property
    def clean(self) -> bool:
        """“已扫描且无命中”。未配置词表时返回 False——没有结论不等于通过。"""
        return self.configured and not self.hits


def load_terms(env: dict[str, str] | None = None) -> tuple[str, ...]:
    source = os.environ if env is None else env
    raw = source.get(TERMS_ENV, "")
    return tuple(term.strip() for term in raw.split(",") if term.strip())


def scan_text(text: str, terms: tuple[str, ...] | None = None) -> ScanResult:
    """返回疑似命中。空词表 → 未配置，不产生任何“通过”含义。"""
    configured_terms = load_terms() if terms is None else terms
    if not configured_terms:
        return ScanResult(configured=False, hits=())
    hits: list[Hit] = []
    lowered = text.lower()
    for term in configured_terms:
        needle = term.lower()
        start = 0
        while True:
            found = lowered.find(needle, start)
            if found < 0:
                break
            hits.append(Hit(term=term, offset=found))
            start = found + max(1, len(needle))
    return ScanResult(configured=True, hits=tuple(sorted(hits, key=lambda h: h.offset)))
