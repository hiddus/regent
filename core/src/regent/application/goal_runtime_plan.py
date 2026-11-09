"""Runtime plan / fork / failure-lesson helpers (run-think-learn L1–L3).

Pure functions + small persistence helpers. No second fact source — results
land on GoalSpec / goal.metadata / conversation messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from regent.application.p1_contracts import canonical_hash

_MAX_FAILURE_LESSONS = 12
_MAX_FORK_OPTIONS = 4


def synthesize_steps(
    *,
    first_deliverable: str,
    problem: str,
    proposed_steps: list[str] | None,
) -> list[str]:
    cleaned = [str(s).strip() for s in (proposed_steps or []) if str(s).strip()]
    if cleaned:
        return cleaned[:8]
    deliverable = (first_deliverable or "可预览的第一版").strip()
    problem_bit = (problem or "用户问题").strip()[:80]
    return [
        f"澄清并锁定首轮交付：{deliverable}",
        f"围绕「{problem_bit}」收集证据 / 约束",
        "按人步实现最小可验证版本",
        "对照成功标准做 Preview 验证",
    ]


def synthesize_fork_options(
    *,
    unknowns: list[str],
    fork_options: list[dict[str, Any]] | None,
    deduction_clear: bool,
) -> list[dict[str, str]]:
    raw = []
    for item in fork_options or []:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not oid or not label:
            continue
        raw.append(
            {
                "id": oid[:64],
                "label": label[:120],
                "description": str(item.get("description") or "")[:400],
            }
        )
    if raw:
        return raw[:_MAX_FORK_OPTIONS]
    if deduction_clear:
        return []
    # Model said unclear but gave no options — derive from unknowns.
    out: list[dict[str, str]] = []
    for idx, q in enumerate((unknowns or [])[:_MAX_FORK_OPTIONS]):
        text = str(q).strip()
        if not text:
            continue
        out.append(
            {
                "id": f"u{idx + 1}",
                "label": text[:80],
                "description": "请确认或否定该未知点，以便继续推演",
            }
        )
    if len(out) >= 2:
        return out
    return [
        {
            "id": "explore_thin",
            "label": "先做最薄可验证原型",
            "description": "在信息不足时先验证核心假设，再扩展范围",
        },
        {
            "id": "clarify_scope",
            "label": "先收窄范围再做",
            "description": "优先砍掉非必要能力，只保留首轮交付",
        },
        {
            "id": "user_priority",
            "label": "由我补充优先级后再继续",
            "description": "你补充最重要的约束或用户群后，系统再拆方案",
        },
    ][:_MAX_FORK_OPTIONS]


def build_runtime_plan(
    *,
    app_name: str,
    product_intent: str,
    target_users: str,
    problem: str,
    first_deliverable: str,
    success_criteria: dict[str, Any],
    unknowns: list[str],
    proposed_steps: list[str] | None = None,
    deduction_clear: bool = True,
    fork_options: list[dict[str, Any]] | None = None,
    non_goals: list[str] | None = None,
) -> dict[str, Any]:
    """Build console-facing plan payload + fork gate flag."""
    steps = synthesize_steps(
        first_deliverable=first_deliverable,
        problem=problem,
        proposed_steps=proposed_steps,
    )
    unknown_list = [str(u).strip() for u in (unknowns or []) if str(u).strip()]
    # Unclear if model says so, or many open unknowns with no steps from model.
    model_steps = [str(s).strip() for s in (proposed_steps or []) if str(s).strip()]
    unclear = (not deduction_clear) or (
        len(unknown_list) >= 3 and not model_steps
    )
    options = synthesize_fork_options(
        unknowns=unknown_list,
        fork_options=fork_options,
        deduction_clear=not unclear,
    )
    needs_user_fork = unclear and len(options) >= 2
    return {
        "app_name": app_name,
        "product_intent": product_intent,
        "target_users": target_users,
        "problem": problem,
        "first_deliverable": first_deliverable,
        "success_criteria": dict(success_criteria or {}),
        "non_goals": list(non_goals or []),
        "proposed_steps": steps,
        "unknowns": unknown_list,
        "fork_options": options if needs_user_fork else [],
        "needs_user_fork": needs_user_fork,
        "deduction_clear": not needs_user_fork,
    }


def append_failure_lesson(
    metadata: dict[str, Any] | None,
    *,
    code: str,
    summary: str,
    avoid: str,
    gap_kind: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a structured lesson; dedupe by digest; cap list length."""
    meta = dict(metadata or {})
    lesson = {
        "at": datetime.now(UTC).isoformat(),
        "code": str(code)[:128],
        "summary": str(summary)[:400],
        "avoid": str(avoid)[:400],
        "gap_kind": str(gap_kind or code)[:64],
        **(extra or {}),
    }
    lesson["lesson_digest"] = canonical_hash(
        {
            "code": lesson["code"],
            "summary": lesson["summary"],
            "avoid": lesson["avoid"],
            "gap_kind": lesson["gap_kind"],
        }
    )[:24]
    prior = list(meta.get("failure_lessons") or [])
    digests = {str(x.get("lesson_digest")) for x in prior if isinstance(x, dict)}
    if lesson["lesson_digest"] not in digests:
        prior.append(lesson)
    meta["failure_lessons"] = prior[-_MAX_FAILURE_LESSONS:]
    return meta


def _normalize_failure_lesson(item: dict[str, Any]) -> dict[str, Any] | None:
    """Accept new (summary/avoid) and legacy gap (gap_reasons/constraints) shapes."""
    summary = str(item.get("summary") or "").strip()
    avoid = str(item.get("avoid") or "").strip()
    gap_reasons = [
        str(r).strip() for r in list(item.get("gap_reasons") or []) if str(r).strip()
    ]
    constraints = [
        str(c).strip()
        for c in list(item.get("learned_constraints") or [])
        if str(c).strip()
    ]
    last_error = str(item.get("last_error") or "").strip()
    halt_message = str(item.get("halt_message") or "").strip()

    if not summary:
        bits = gap_reasons[:3] or ([last_error] if last_error else []) or (
            [halt_message] if halt_message else []
        )
        summary = "; ".join(bits)[:400]
    if not avoid:
        if constraints:
            avoid = "; ".join(constraints[:4])[:400]
        elif gap_reasons or last_error or halt_message:
            avoid = "下次须避开本轮 gap_reasons / last_error，并满足 learned_constraints"

    # Drop empty noise / non-lesson dicts.
    if not (summary or avoid or gap_reasons or constraints or item.get("lesson_digest")):
        return None

    out = dict(item)
    if summary:
        out["summary"] = summary[:400]
    if avoid:
        out["avoid"] = avoid[:400]
    return out


def lessons_for_acceptance(metadata: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    """Return failure lessons for acceptance_contract (legacy + new shapes)."""
    raw = list((metadata or {}).get("failure_lessons") or [])
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_failure_lesson(item)
        if normalized is not None:
            out.append(normalized)
    return out[-limit:]
