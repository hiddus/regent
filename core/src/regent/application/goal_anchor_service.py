"""GoalAnchor — 目标锚点机制 (GAC-GA).

横切关注点：确保原始目标文本贯穿全流程，不被中间表示稀释。
在 Generation 阶段注入原始目标，在 Delivery Review 阶段验证目标对齐。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class GoalAlignmentResult:
    """Goal alignment validation result."""

    aligned: bool
    score: float  # 0.0 ~ 1.0
    details: list[str] = field(default_factory=list)


# Stop-words that should not count as goal keywords
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "it", "this", "that", "are",
    "was", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "can", "shall",
    "not", "no", "nor", "so", "if", "then", "than", "too", "very",
    "just", "about", "into", "over", "after", "each", "every", "all",
    "both", "few", "more", "most", "other", "some", "such", "only",
    "own", "same", "when", "what", "which", "who", "whom", "how", "where",
    "why", "up", "out", "off", "down", "near", "also", "its",
    # Chinese stop words
    "\u7684", "\u4e86", "\u5728", "\u662f", "\u6211", "\u6709", "\u548c", "\u5c31", "\u4e0d", "\u4eba", "\u90fd",
    "\u4e00", "\u4e00\u4e2a", "\u4e0a", "\u4e5f", "\u5f88", "\u5230", "\u8bf4", "\u8981", "\u53bb", "\u4f60",
    "\u4f1a", "\u7740", "\u6ca1\u6709", "\u770b", "\u597d", "\u81ea\u5df1", "\u8fd9", "\u4ed6", "\u5979",
    "\u80fd", "\u591f", "\u6700", "\u505a", "\u628a", "\u88ab", "\u8ba9", "\u7ed9",
})


def extract_goal_keywords(goal_text: str) -> list[str]:
    """Extract meaningful keywords from goal text.

    Handles both English (space-separated) and Chinese (character-based)
    text. Chinese is segmented into 2-3 character bi-grams/tri-grams.
    """
    seen: set[str] = set()
    keywords: list[str] = []

    def _add(token: str) -> None:
        t = token.lower().strip()
        if (
            len(t) >= 2
            and t not in _STOP_WORDS
            and t not in seen
            and not t.isdigit()
        ):
            seen.add(t)
            keywords.append(t)

    # 1. Extract English words (ASCII letters/digits)
    for match in re.findall(r"[a-zA-Z]{2,}", goal_text):
        _add(match)

    # 2. Extract Chinese segments using sliding window (bi-grams + tri-grams)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", goal_text)
    for run in chinese_runs:
        # Bi-grams
        for i in range(len(run) - 1):
            _add(run[i : i + 2])
        # Tri-grams
        for i in range(len(run) - 2):
            _add(run[i : i + 3])

    return keywords


def build_goal_anchored_prompt(
    base_prompt: str,
    *,
    goal_text: str,
    success_criteria: dict | None = None,
    first_deliverable: str = "",
    retry_context: str = "",
) -> str:
    """Enrich a generation plan dict with goal anchor information.

    Returns a new dict with a ``_goal_anchor`` section injected,
    plus the user_prompt enriched with explicit goal text.
    """
    anchor_block = (
        "\n\n"
        "═══════════════════════════════════════════════════════\n"
        "GOAL ANCHOR — THIS IS YOUR PRIMARY OBJECTIVE\n"
        "═══════════════════════════════════════════════════════\n"
        f"\nORIGINAL USER GOAL: {goal_text}\n"
    )
    if first_deliverable:
        anchor_block += f"\nFIRST DELIVERABLE: {first_deliverable}\n"
    if success_criteria:
        criteria_lines = "\n".join(
            f"  - {k}: {v}" for k, v in success_criteria.items()
        )
        anchor_block += f"\nSUCCESS CRITERIA:\n{criteria_lines}\n"
    if retry_context:
        anchor_block += f"\n⚠️ RETRY — PREVIOUS ATTEMPT FAILED:\n{retry_context}\n"
    anchor_block += (
        "\nCRITICAL: Every file you generate MUST directly serve this goal. "
        "If the goal says 'timestamp', your page MUST show a timestamp. "
        "If the goal says 'news digest', your page MUST show news items. "
        "Do NOT generate unrelated templates, forms, or demo stubs.\n"
        "═══════════════════════════════════════════════════════\n"
    )
    return base_prompt + anchor_block


def validate_goal_alignment(
    html: str,
    goal_text: str,
    *,
    success_criteria: dict | None = None,
    first_deliverable: str = "",
) -> GoalAlignmentResult:
    """Validate that generated HTML aligns with the original goal.

    Uses keyword-based heuristic checks. This is a fast, deterministic
    gate — not a replacement for LLM-based review, but a first line
    of defense against completely off-target generation.
    """
    details: list[str] = []
    visible = re.sub(r"<[^>]+>", " ", html)
    visible = re.sub(r"\s+", " ", visible).strip().lower()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title_text = title_match.group(1).strip().lower() if title_match else ""
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    h1_text = h1_match.group(1).strip().lower() if h1_match else ""

    # 1. Goal keyword alignment
    keywords = extract_goal_keywords(goal_text)
    if keywords:
        hits = sum(
            1 for kw in keywords
            if kw in visible or kw in title_text or kw in h1_text
        )
        keyword_score = hits / len(keywords) if keywords else 0.0
        details.append(
            f"goal keywords: {hits}/{len(keywords)} matched "
            f"({keyword_score:.0%})"
        )
    else:
        keyword_score = 0.5  # neutral if no keywords extracted
        details.append("no keywords extracted from goal")

    # 2. First deliverable alignment
    deliverable_score = 0.5
    if first_deliverable:
        deliverable_keywords = extract_goal_keywords(first_deliverable)
        if deliverable_keywords:
            d_hits = sum(
                1 for kw in deliverable_keywords
                if kw in visible or kw in title_text
            )
            deliverable_score = d_hits / len(deliverable_keywords)
            details.append(
                f"deliverable keywords: {d_hits}/{len(deliverable_keywords)} "
                f"({deliverable_score:.0%})"
            )

    # 3. Success criteria alignment
    criteria_score = 0.5
    if success_criteria:
        criteria_hits = 0
        criteria_total = 0
        for key, value in success_criteria.items():
            criteria_total += 1
            key_lower = key.lower().replace("_", " ")
            # Check if the criterion concept appears in the HTML
            criterion_words = extract_goal_keywords(key_lower)
            if criterion_words:
                if any(w in visible for w in criterion_words):
                    criteria_hits += 1
            elif isinstance(value, bool) and value:
                # Boolean criteria — check key name in HTML
                if key_lower in visible or key.replace("_", "") in visible.replace(" ", ""):
                    criteria_hits += 1
        if criteria_total > 0:
            criteria_score = criteria_hits / criteria_total
            details.append(
                f"success criteria: {criteria_hits}/{criteria_total} "
                f"({criteria_score:.0%})"
            )

    # 4. Anti-pattern detection: page title completely unrelated to goal
    anti_pattern_penalty = 0.0
    if title_text and keywords:
        title_keywords = extract_goal_keywords(title_text)
        overlap = set(title_keywords) & set(keywords)
        if not overlap and len(title_keywords) >= 2:
            # Title has keywords but none overlap with goal — likely unrelated
            anti_pattern_penalty = 0.3
            details.append(
                f"title '{title_text}' has no keyword overlap with goal "
                "(likely unrelated content)"
            )

    # Weighted composite score
    # When no deliverable/criteria context, keyword match is the primary signal.
    has_context = bool(first_deliverable) or bool(success_criteria)
    if has_context:
        score = (
            keyword_score * 0.4
            + deliverable_score * 0.3
            + criteria_score * 0.3
            - anti_pattern_penalty
        )
    else:
        # Without context, keyword match is everything
        score = keyword_score - anti_pattern_penalty
    score = max(0.0, min(1.0, score))

    aligned = score >= 0.15  # Threshold: at least 15% alignment
    if not aligned:
        details.append(
            f"ALIGNMENT FAILED: composite score {score:.0%} < 15% threshold"
        )

    return GoalAlignmentResult(
        aligned=aligned,
        score=score,
        details=details,
    )


# ---------------------------------------------------------------------------
# LLM-based semantic alignment validation
# ---------------------------------------------------------------------------

class _SemanticAlignmentResponse(BaseModel):
    """LLM response for semantic goal alignment check."""
    aligned: bool = Field(description="Whether the HTML achieves the user's goal")
    reasoning: str = Field(default="", description="Brief explanation")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence 0-1")


_SEMANTIC_ALIGNMENT_PROMPT = """You are a quality assurance evaluator. Your job is to determine
whether a generated web page ACTUALLY ACHIEVES the user's original goal.

You will receive:
1. The user's ORIGINAL GOAL
2. The FIRST DELIVERABLE description
3. The generated HTML content

Evaluate whether the HTML content genuinely serves the user's goal. Consider:
- Does the page content match what the goal describes?
- Would a user looking at this page feel their goal was achieved?
- Are the key concepts/features from the goal present in the page?

Be strict but fair. A page about "timestamp" that shows a clock is aligned.
A page about "timestamp" that shows a contact form is NOT aligned.
A page about "AI community" that shows a Japanese chatbot is NOT aligned.

Return your assessment as a JSON object with:
- aligned: true/false
- reasoning: brief explanation
- confidence: 0.0-1.0"""


async def validate_goal_alignment_semantic(
    html: str,
    goal_text: str,
    *,
    provider: Any,
    first_deliverable: str = "",
) -> GoalAlignmentResult:
    """Use an LLM to semantically validate HTML against the original goal.

    This is a slower but much more accurate check than keyword-based
    ``validate_goal_alignment``.  Call it when keyword check is borderline
    or as a final gate before delivery.
    """
    user_content = f"ORIGINAL GOAL:\n{goal_text}\n"
    if first_deliverable:
        user_content += f"\nFIRST DELIVERABLE:\n{first_deliverable}\n"
    # Truncate HTML to avoid token limits — first 8000 chars is enough
    # for the LLM to understand the page structure and content.
    user_content += f"\nGENERATED HTML (truncated):\n{html[:8000]}"

    try:
        response = await provider.generate_structured(
            system_prompt=_SEMANTIC_ALIGNMENT_PROMPT,
            user_prompt=user_content,
            response_model=_SemanticAlignmentResponse,
        )
        result = response.output
        score = result.confidence if result.aligned else (1.0 - result.confidence) * 0.3
        details = [f"LLM semantic check: {result.reasoning}"]
        return GoalAlignmentResult(
            aligned=result.aligned,
            score=max(0.0, min(1.0, score)),
            details=details,
        )
    except Exception:
        # If the LLM check fails, fall back to keyword-based validation
        return validate_goal_alignment(
            html, goal_text, first_deliverable=first_deliverable,
        )
