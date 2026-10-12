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


def chapter_canon_blockers(run: Any, production: dict[str, Any]) -> list[str]:
    """正史提交前的确定性硬门（工作包1.2）。

    review.passed 与 creative_accept 都不能单独替代本校验。返回阻断原因列表；
    非空即禁止写入正史/正式记忆。只校验可确定性核对的项：
    - 没有未结硬问题（含证据缺陷）；
    - 正文版本与最近一次审校证据一致；
    - accepted 下标唯一、有效且非失效 take。
    """
    from regent.novel.domain.review_issues import partition_issues

    blockers: list[str] = []
    review = getattr(run, "review", None)
    review = review if isinstance(review, dict) else {}
    content = str(getattr(run, "content", "") or "")

    if "hard_issues" in review:
        hard = [str(x) for x in (review.get("hard_issues") or []) if str(x).strip()]
    else:
        hard, _ = partition_issues(review.get("continuity_issues") or [])
    if hard:
        blockers.append(f"unresolved_hard_issues:{len(hard)}")

    history = production.get("review_history") or []
    if history and isinstance(history[-1], dict):
        review_hash = str(history[-1].get("content_hash") or "")
        if review_hash and review_hash != content_hash(content):
            blockers.append("review_hash_mismatch")

    accepted = production.get("accepted") or []
    takes = production.get("takes") or []
    idxs = [int(i) for i in accepted]
    if len(set(idxs)) != len(idxs):
        blockers.append("duplicate_accepted_index")
    for i in idxs:
        if not (0 <= i < len(takes)):
            blockers.append("accepted_index_out_of_range")
            break
        if str((takes[i] or {}).get("status") or "") in {"SUPERSEDED", "REJECTED"}:
            blockers.append("accepted_take_invalidated")
            break
    return blockers


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
    from regent.novel.domain.chapter_handoff import build_chapter_handoff

    passed = bool(run.review.get("passed")) if review_passed is None else bool(review_passed)
    facts = collect_accepted_facts(production)
    history = production.get("review_history") or []
    review_hash = ""
    if history and isinstance(history[-1], dict):
        review_hash = str(history[-1].get("content_hash") or "")
    state = production.get("working_state")
    ledger = production.get("continuity_ledger")
    handoff = build_chapter_handoff(
        chapter_no=int(getattr(run, "chapter_no", 0) or 0),
        content=str(run.content or ""),
        actual_state=state if isinstance(state, dict) else {},
        review_content_hash=review_hash,
        continuity_ledger=ledger if isinstance(ledger, dict) else None,
    )
    blockers = chapter_canon_blockers(run, production)
    run.generation_context = {
        **run.generation_context,
        "verified_facts": facts,
        "actual_state": state,
        "node_completed": node_completed,
        "validated_content_hash": content_hash(str(run.content or "")),
        "chapter_handoff": handoff,
        "canon_blockers": blockers,
    }
    # 硬门优先于 review.passed：有未结硬问题/版本不符/take 失效时不写正史与正式记忆。
    if passed and not blockers:
        commit_run_story_ledger(run, production=production, facts=facts)
