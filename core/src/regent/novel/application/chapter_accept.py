"""Chapter final-accept boundary: prose hash, facts, and story ledger.

Editorial local repair must run *before* ``finalize_chapter_acceptance``.
Do not commit the ledger and then rewrite ``run.content``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def commit_run_story_ledger(
    run: Any,
    *,
    production: dict[str, Any],
    facts: list[Any],
) -> None:
    """Persist cross-chapter story ledger after independent chapter review passes."""
    from regent.novel.application.generation import commit_story_ledger

    sp = production.get("script_protocol") or {}
    hooks_opened: list[str] = []
    hooks_closed: list[str] = []
    shifts: list[str] = []
    for audit in (sp.get("scene_audits") or {}).values():
        if not isinstance(audit, dict):
            continue
        hooks_opened.extend(str(h) for h in (audit.get("hooks_opened") or []) if h)
        hooks_closed.extend(str(h) for h in (audit.get("hooks_closed") or []) if h)
        shifts.extend(str(s) for s in (audit.get("character_shifts") or []) if s)
    cast = production.get("cast") or {}
    protagonist = next(iter(cast), "") if isinstance(cast, dict) else ""
    commit_story_ledger(
        run,
        facts=facts,
        hooks_opened=hooks_opened,
        hooks_closed=hooks_closed,
        character_shifts=shifts,
        protagonist=str(protagonist or ""),
    )


def collect_accepted_facts(production: dict[str, Any]) -> list[Any]:
    return [
        fact
        for i in production.get("accepted") or []
        for fact in (production["takes"][i].get("validation") or {}).get("facts") or []
    ]


def fact_quotes_missing(content: str, facts: Sequence[Any]) -> list[str]:
    """Return fact quotes that no longer appear verbatim in content."""
    missing: list[str] = []
    text = content or ""
    for fact in facts:
        if isinstance(fact, dict):
            quote = str(fact.get("quote") or "")
        else:
            quote = str(getattr(fact, "quote", "") or "")
        if quote and quote not in text:
            missing.append(quote[:80])
    return missing


def post_apply_safety_ok(
    *,
    candidate: str,
    production: dict[str, Any],
    front_fails: Sequence[str] | None = None,
    completion_quote: str = "",
) -> tuple[bool, list[str]]:
    """Deterministic gate after auto-apply; fail → keep original, do not finalize new prose."""
    notes: list[str] = []
    if front_fails:
        notes.extend(str(x) for x in front_fails[:6])
    facts = collect_accepted_facts(production)
    missing = fact_quotes_missing(candidate, facts)
    if missing:
        notes.append(f"fact_quotes_missing:{len(missing)}")
    cq = (completion_quote or "").strip()
    if cq and cq not in candidate:
        notes.append("completion_quote_missing")
    return (not notes), notes


def finalize_chapter_acceptance(
    run: Any,
    production: dict[str, Any],
    *,
    node_completed: Any = None,
    review_passed: bool | None = None,
) -> None:
    """Single entry for accepting chapter prose, facts, and ledger.

    Call only after review (and any future editorial repair) has finished.
    """
    passed = bool(run.review.get("passed")) if review_passed is None else bool(review_passed)
    facts = collect_accepted_facts(production)
    run.generation_context = {
        **run.generation_context,
        "verified_facts": facts,
        "actual_state": production.get("working_state"),
        "node_completed": node_completed,
        "validated_content_hash": content_hash(str(run.content or "")),
    }
    if passed:
        commit_run_story_ledger(run, production=production, facts=facts)
