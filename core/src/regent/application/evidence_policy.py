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


# ---------------------------------------------------------------------------
# G2 / Spec §12: Evidence trust classification (five classes)
# ---------------------------------------------------------------------------

EVIDENCE_CLASS_DECLARED_INTENT = "declared-intent"
EVIDENCE_CLASS_SOURCED_OBSERVATION = "sourced-observation"
EVIDENCE_CLASS_BUILD_VERIFICATION = "build-verification"
EVIDENCE_CLASS_PRODUCT_OBSERVATION = "product-observation"
EVIDENCE_CLASS_OPERATIONAL_OBSERVATION = "operational-observation"

# Legacy aliases retained for connectors / prompts.
LEGACY_DECLARED_INTENT = "DECLARED_INTENT"
LEGACY_UNTRUSTED_DATA = "UNTRUSTED_DATA"

_PRODUCT_GATE_ELIGIBLE = frozenset(
    {
        EVIDENCE_CLASS_SOURCED_OBSERVATION,
        EVIDENCE_CLASS_BUILD_VERIFICATION,
        EVIDENCE_CLASS_PRODUCT_OBSERVATION,
        LEGACY_UNTRUSTED_DATA,  # external sourced data may support claims, not alone for market
    }
)

_PRODUCT_GATE_FORBIDDEN = frozenset(
    {
        EVIDENCE_CLASS_OPERATIONAL_OBSERVATION,
        EVIDENCE_CLASS_DECLARED_INTENT,
        LEGACY_DECLARED_INTENT,
    }
)


def classify_evidence(snapshot: object) -> str:
    """Return Spec §12 evidence class for an EvidenceSourceSnapshot.

    Five classes:
    - declared-intent
    - sourced-observation
    - build-verification
    - product-observation
    - operational-observation

    Legacy ``DECLARED_INTENT`` / ``UNTRUSTED_DATA`` labels are normalized to the
    five-class vocabulary (declared-intent / sourced-observation).
    """
    kind = ""
    source_type = ""
    trust_label = ""
    metadata: dict[str, Any] = {}
    if hasattr(snapshot, "metadata"):
        metadata = dict(getattr(snapshot, "metadata") or {})
        kind = str(metadata.get("kind") or "").lower()
    if hasattr(snapshot, "source_type"):
        source_type = str(getattr(snapshot, "source_type") or "").lower()
    if hasattr(snapshot, "trust_label"):
        trust_label = str(getattr(snapshot, "trust_label") or "")

    combined = f"{kind} {source_type} {metadata.get('class', '')}".lower()

    if kind == "goal-intent" or source_type == "goal-intent" or trust_label == LEGACY_DECLARED_INTENT:
        return EVIDENCE_CLASS_DECLARED_INTENT
    if any(
        token in combined
        for token in ("operational", "smoke", "monitor", "internal-traffic", "healthcheck")
    ):
        return EVIDENCE_CLASS_OPERATIONAL_OBSERVATION
    if any(token in combined for token in ("build", "test-report", "ci-verification")):
        return EVIDENCE_CLASS_BUILD_VERIFICATION
    if any(
        token in combined
        for token in ("product-observation", "user-feedback", "real-user", "analytics-event")
    ):
        return EVIDENCE_CLASS_PRODUCT_OBSERVATION
    if kind in {"http-snapshot", "search-result", "web-content"} or trust_label == LEGACY_UNTRUSTED_DATA:
        return EVIDENCE_CLASS_SOURCED_OBSERVATION
    if kind:
        return EVIDENCE_CLASS_SOURCED_OBSERVATION
    return EVIDENCE_CLASS_SOURCED_OBSERVATION


def evidence_may_satisfy_product_gate(classification: str) -> bool:
    """Hard rule: operational-observation / declared-intent must NOT satisfy product Gate."""
    normalized = classification.strip()
    if normalized in _PRODUCT_GATE_FORBIDDEN:
        return False
    if normalized in {
        EVIDENCE_CLASS_OPERATIONAL_OBSERVATION,
        "OPERATIONAL_OBSERVATION",
        "operational_observation",
    }:
        return False
    return normalized in _PRODUCT_GATE_ELIGIBLE or normalized in {
        "SOURCED_OBSERVATION",
        "BUILD_VERIFICATION",
        "PRODUCT_OBSERVATION",
    }


def classify_evidence_legacy_label(snapshot: object) -> str:
    """Compatibility helper used by older tests expecting DECLARED_INTENT/UNTRUSTED_DATA."""
    cls = classify_evidence(snapshot)
    if cls == EVIDENCE_CLASS_DECLARED_INTENT:
        return LEGACY_DECLARED_INTENT
    return LEGACY_UNTRUSTED_DATA
