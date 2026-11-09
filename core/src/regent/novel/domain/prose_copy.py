"""跨段照抄检测：连续相同原文，以及可以保留的短引语或铭文。"""

from __future__ import annotations

from typing import Any


def longest_common_substring(a: str, b: str, *, min_len: int = 80) -> str:
    """找两个字符串的最长公共子串（达到 ``min_len`` 才返回）。"""
    if not a or not b or min_len < 1:
        return ""
    la, lb = len(a), len(b)
    best = ""
    for i in range(la):
        for j in range(lb):
            k = 0
            while i + k < la and j + k < lb and a[i + k] == b[j + k]:
                k += 1
            if k >= min_len and k > len(best):
                best = a[i : i + k]
    return best


def is_legitimate_repetition(text: str, ledger: dict[str, Any] | None = None) -> bool:
    """长重复只在整段就是固定铭文，或不足 40 字的引语时放行。

    片段里出现引号或「说」「道」不能把整段照抄算成引用。
    """
    stripped = text.strip()
    if not stripped:
        return False
    if ledger and isinstance(ledger, dict):
        for prop in (ledger.get("props") or {}).values():
            if not isinstance(prop, dict):
                continue
            for partition_text in (prop.get("partitions") or {}).values():
                inscription = str(partition_text or "").strip()
                if inscription and stripped == inscription:
                    return True
    if len(stripped) <= 40 and stripped[0] in "「『\"'":
        return True
    return False
