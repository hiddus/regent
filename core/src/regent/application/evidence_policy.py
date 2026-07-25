"""Evidence authorization policy: Core owns the port, not product feeds."""

from __future__ import annotations

import json
import re
from typing import Any

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
# Markers must imply *external* sourced content. Do NOT include bare "摘要"/summary —
# that falsely forces RESEARCH_MORE + default RSS onto paste/summarize Goals.
_EXTERNAL_NEED_MARKERS = (
    "news",
    "digest",
    "rss",
    "atom",
    "headline",
    "headlines",
    "feed",
    "feeds",
    "scrape",
    "crawl",
    "aggregator",
    "aggregation",
    "research report",
    "行情",
    "新闻",
    "资讯",
    "动态",
    "趋势",
    "热点",
    "快讯",
    "头条",
    "前沿",
    "情报",
    "信息流",
    "rss源",
    "订阅源",
)


def extract_urls_from_text(text: str) -> list[str]:
    """Extract http(s) URLs from free text (guidance, Goal, etc.)."""
    found = [match.rstrip(").,;]") for match in _URL_RE.findall(text or "")]
    return list(dict.fromkeys(found))


def collect_authorized_urls(goal: str, constraints: dict[str, Any] | None = None) -> list[str]:
    """Collect fetch targets only from Goal / constraints — never Core-owned seeds."""
    found = extract_urls_from_text(goal or "")
    if constraints:
        blob = json.dumps(constraints, ensure_ascii=False)
        found.extend(extract_urls_from_text(blob))
        nested = constraints.get("authorized_source_urls")
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, str) and item.strip():
                    found.append(item.strip())
        elif isinstance(nested, str) and nested.strip():
            found.extend(extract_urls_from_text(nested))
    return list(dict.fromkeys(found))


def goal_requires_external_evidence(
    goal: str, constraints: dict[str, Any] | None = None
) -> bool:
    """Heuristic: product needs observed external content, not Goal-text alone."""
    parts = [goal or ""]
    if constraints:
        parts.append(json.dumps(constraints, ensure_ascii=False))
    text = " ".join(parts).lower()
    return any(marker in text for marker in _EXTERNAL_NEED_MARKERS)
