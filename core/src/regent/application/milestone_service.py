"""GAC-E: goal-driven milestone decomposition.

Large Goals must not ACHIEVE in a single delivery loop. Milestone count and content are
derived from the Goal / GoalSpec — not a fixed M1/M2/M3 template.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.infrastructure.models import GoalModel, GoalSpecModel, WorkModel

GOAL_SCALE_SMALL = "SMALL"
GOAL_SCALE_LARGE = "LARGE"

_MILESTONE_KIND = "milestone"
_DERIVATION = "goal-driven-v1"
_MAX_MILESTONES = 8
_MULTI_SIGNAL_RE = re.compile(
    r"平台|系统|完整|阶段|里程碑|多模块|多端|同时|以及|并且|"
    r"platform|system|full[- ]?stack|multi[- ]|phases?|milestones?|"
    r"and then|end[- ]to[- ]end",
    re.I,
)
_SPLIT_RE = re.compile(
    r"[；;。\n]+|"
    r"(?:^|[，,、]\s*)(?:然后|接着|其次|最后|同时|以及|并且|并|"
    r"then|next|also|and\s+then)\s*",
    re.I,
)
# Success-criteria keys that are structural, not a distinct deliverable slice.
_CRITERIA_META_KEYS = {
    "first_deliverable",
    "milestones",
    "milestone_plan",
    "usable",
    "preview_scope",
}


@dataclass(frozen=True, slots=True)
class MilestoneSpec:
    ordinal: int
    key: str
    title: str
    acceptance: dict[str, Any]
    is_final: bool


@dataclass(frozen=True, slots=True)
class MilestonePlan:
    goal_scale: str
    milestones: tuple[MilestoneSpec, ...]
    current_ordinal: int


@dataclass(frozen=True, slots=True)
class _GoalSlice:
    title: str
    deliverable: str
    acceptance: dict[str, Any]
    source: str


def classify_goal_scale(
    original_input: str,
    success_criteria: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
    *,
    system_inferences: dict[str, Any] | None = None,
    unknowns: list[Any] | None = None,
) -> str:
    """Decide SMALL vs LARGE from Goal content. Explicit metadata wins."""
    meta = dict(metadata or {})
    forced = str(meta.get("goal_scale") or "").upper()
    if forced in {GOAL_SCALE_SMALL, GOAL_SCALE_LARGE}:
        return forced
    if meta.get("force_milestones") is True:
        return GOAL_SCALE_LARGE

    text = (original_input or "").strip()
    criteria = dict(success_criteria or {})
    inferences = dict(system_inferences or {})
    unknown_count = len(unknowns or [])
    signals = 0
    if len(text) >= 120:
        signals += 1
    if len([k for k in criteria if k not in _CRITERIA_META_KEYS]) >= 2:
        signals += 1
    if len(_MULTI_SIGNAL_RE.findall(text)) >= 2:
        signals += 1
    first = str(
        criteria.get("first_deliverable") or meta.get("first_deliverable") or ""
    ).strip()
    if len(first) >= 80:
        signals += 1
    if text.count("、") + text.count(",") >= 3:
        signals += 1
    if unknown_count >= 2:
        signals += 1
    if inferences.get("milestones") or inferences.get("deliverables"):
        signals += 2
    if _explicit_milestone_list(meta, criteria, inferences):
        signals += 2
    # Multiple natural clauses already imply decomposition.
    if len(_split_goal_clauses(text)) >= 3:
        signals += 1
    return GOAL_SCALE_LARGE if signals >= 2 else GOAL_SCALE_SMALL


def _explicit_milestone_list(
    metadata: dict[str, Any],
    criteria: dict[str, Any],
    inferences: dict[str, Any],
) -> list[Any]:
    for source in (
        metadata.get("requested_milestones"),
        criteria.get("milestones"),
        criteria.get("milestone_plan"),
        inferences.get("milestones"),
        inferences.get("deliverables"),
    ):
        if isinstance(source, list) and source:
            return list(source)
    return []


def _split_goal_clauses(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = [p.strip(" ，,、.-") for p in _SPLIT_RE.split(raw) if p and p.strip()]
    # Also split long Chinese lists joined only by顿号 when enough items.
    if len(parts) <= 1 and ("、" in raw or "," in raw):
        parts = [p.strip() for p in re.split(r"[、,]", raw) if p.strip()]
    # Drop tiny fragments.
    return [p for p in parts if len(p) >= 4][:_MAX_MILESTONES]


def _slug(text: str, ordinal: int) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text.strip().lower())
    cleaned = cleaned.strip("-")[:40] or f"slice-{ordinal}"
    return f"m{ordinal}-{cleaned}"


def derive_goal_slices(
    *,
    original_input: str,
    success_criteria: dict[str, Any] | None,
    first_deliverable: str | None,
    system_inferences: dict[str, Any] | None = None,
    unknowns: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[_GoalSlice]:
    """Extract ordered deliverable slices from the Goal itself."""
    criteria = dict(success_criteria or {})
    inferences = dict(system_inferences or {})
    meta = dict(metadata or {})
    deliverable = (
        str(first_deliverable or criteria.get("first_deliverable") or "").strip()
        or (original_input[:160].strip() if original_input else "shippable outcome")
    )
    slices: list[_GoalSlice] = []

    # 1) Explicit milestone / deliverable list on GoalSpec or metadata.
    for raw in _explicit_milestone_list(meta, criteria, inferences):
        if isinstance(raw, str) and raw.strip():
            slices.append(
                _GoalSlice(
                    title=raw.strip()[:120],
                    deliverable=raw.strip(),
                    acceptance={"first_deliverable": raw.strip()},
                    source="explicit_list",
                )
            )
        elif isinstance(raw, dict):
            title = str(raw.get("title") or raw.get("name") or raw.get("key") or "").strip()
            body = str(
                raw.get("first_deliverable")
                or raw.get("deliverable")
                or raw.get("purpose")
                or title
            ).strip()
            if not (title or body):
                continue
            acceptance = dict(raw.get("acceptance") or {})
            if body and "first_deliverable" not in acceptance:
                acceptance["first_deliverable"] = body
            for key in ("min_list_items", "min_outbound_links", "required_phrases"):
                if key in raw and key not in acceptance:
                    acceptance[key] = raw[key]
            slices.append(
                _GoalSlice(
                    title=(title or body)[:120],
                    deliverable=body or title,
                    acceptance=acceptance,
                    source="explicit_list",
                )
            )
    if slices:
        return slices[:_MAX_MILESTONES]

    # 2) Natural-language clauses from the Goal text.
    clauses = _split_goal_clauses(original_input)
    if len(clauses) >= 2:
        for clause in clauses:
            slices.append(
                _GoalSlice(
                    title=clause[:120],
                    deliverable=clause,
                    acceptance={"first_deliverable": clause},
                    source="goal_clause",
                )
            )
        return slices[:_MAX_MILESTONES]

    # 3) Distinct success_criteria feature keys → one slice each.
    feature_keys = [k for k in criteria if k not in _CRITERIA_META_KEYS]
    if len(feature_keys) >= 2:
        for key in feature_keys:
            value = criteria[key]
            title = f"{key}={value}" if not isinstance(value, (dict, list)) else key
            acceptance: dict[str, Any] = {
                "first_deliverable": f"Satisfy success criterion '{key}' for: {deliverable[:80]}",
                key: value,
            }
            slices.append(
                _GoalSlice(
                    title=str(title)[:120],
                    deliverable=str(title),
                    acceptance=acceptance,
                    source="success_criteria",
                )
            )
        return slices[:_MAX_MILESTONES]

    # 4) Blocking unknowns as discovery/delivery slices when Goal is otherwise thin.
    blocking_unknowns: list[str] = []
    for item in unknowns or []:
        if isinstance(item, dict):
            q = str(item.get("question") or "").strip()
            if q and item.get("blocking"):
                blocking_unknowns.append(q)
        elif isinstance(item, str) and item.strip():
            blocking_unknowns.append(item.strip())
    if len(blocking_unknowns) >= 2:
        for q in blocking_unknowns:
            slices.append(
                _GoalSlice(
                    title=f"Resolve: {q[:100]}",
                    deliverable=q,
                    acceptance={"first_deliverable": q, "unknown_resolution": q},
                    source="unknown",
                )
            )
        return slices[:_MAX_MILESTONES]

    # 5) Fallback: first_deliverable alone (caller may expand for LARGE).
    return [
        _GoalSlice(
            title=deliverable[:120],
            deliverable=deliverable,
            acceptance={"first_deliverable": deliverable},
            source="first_deliverable",
        )
    ]


def propose_milestones(
    *,
    original_input: str,
    success_criteria: dict[str, Any] | None,
    first_deliverable: str | None,
    goal_scale: str,
    system_inferences: dict[str, Any] | None = None,
    unknowns: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[MilestoneSpec, ...]:
    """Build milestone graph from Goal content. Count follows the Goal, not a template."""
    criteria = dict(success_criteria or {})
    deliverable = (
        str(first_deliverable or criteria.get("first_deliverable") or "").strip()
        or (original_input[:160].strip() if original_input else "shippable outcome")
    )
    slices = derive_goal_slices(
        original_input=original_input,
        success_criteria=criteria,
        first_deliverable=deliverable,
        system_inferences=system_inferences,
        unknowns=unknowns,
        metadata=metadata,
    )

    if goal_scale != GOAL_SCALE_LARGE:
        # SMALL: one final milestone = whole Goal.
        primary = slices[0] if slices else _GoalSlice(
            title=deliverable[:120],
            deliverable=deliverable,
            acceptance={"first_deliverable": deliverable},
            source="small",
        )
        acceptance = {
            **dict(primary.acceptance),
            **{k: v for k, v in criteria.items() if k != "first_deliverable"},
            "milestone_source": primary.source,
        }
        return (
            MilestoneSpec(
                ordinal=1,
                key=_slug(primary.title, 1),
                title=primary.title[:160],
                acceptance=acceptance,
                is_final=True,
            ),
        )

    # LARGE: must have ≥2 milestones derived from Goal; never a fixed 3-step template.
    if len(slices) < 2:
        # Minimal goal-derived split: intermediate first_deliverable → full Goal criteria.
        slices = [
            _GoalSlice(
                title=f"First deliverable: {deliverable[:100]}",
                deliverable=deliverable,
                acceptance={
                    "first_deliverable": deliverable,
                    "milestone_scope": "first_deliverable",
                },
                source="derived_first",
            ),
            _GoalSlice(
                title=f"Full Goal attained: {(original_input or deliverable)[:100]}",
                deliverable=original_input.strip()[:200] or deliverable,
                acceptance={
                    "first_deliverable": deliverable,
                    **criteria,
                    "milestone_scope": "full_goal",
                },
                source="derived_full",
            ),
        ]

    slices = slices[:_MAX_MILESTONES]
    milestones: list[MilestoneSpec] = []
    for index, goal_slice in enumerate(slices, start=1):
        is_final = index == len(slices)
        acceptance = dict(goal_slice.acceptance)
        acceptance["milestone_source"] = goal_slice.source
        acceptance["milestone_key"] = _slug(goal_slice.title, index)
        if is_final:
            # Final milestone carries remaining Goal success criteria.
            for key, value in criteria.items():
                if key not in acceptance:
                    acceptance[key] = value
            if "first_deliverable" not in acceptance:
                acceptance["first_deliverable"] = deliverable
        milestones.append(
            MilestoneSpec(
                ordinal=index,
                key=_slug(goal_slice.title, index),
                title=goal_slice.title[:160],
                acceptance=acceptance,
                is_final=is_final,
            )
        )
    return tuple(milestones)


def plan_from_metadata(metadata: dict[str, Any]) -> MilestonePlan | None:
    raw = metadata.get("milestones")
    if not isinstance(raw, list) or not raw:
        return None
    specs: list[MilestoneSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        specs.append(
            MilestoneSpec(
                ordinal=int(item.get("ordinal") or len(specs) + 1),
                key=str(item.get("key") or f"m{len(specs)+1}"),
                title=str(item.get("title") or item.get("key") or "milestone"),
                acceptance=dict(item.get("acceptance") or {}),
                is_final=bool(item.get("is_final")),
            )
        )
    if not specs:
        return None
    specs.sort(key=lambda s: s.ordinal)
    current = int(metadata.get("current_milestone_ordinal") or specs[0].ordinal)
    scale = str(metadata.get("goal_scale") or GOAL_SCALE_SMALL)
    return MilestonePlan(scale, tuple(specs), current)


def current_milestone(plan: MilestonePlan) -> MilestoneSpec:
    for item in plan.milestones:
        if item.ordinal == plan.current_ordinal:
            return item
    return plan.milestones[0]


def is_final_milestone(plan: MilestonePlan) -> bool:
    current = current_milestone(plan)
    return bool(current.is_final) or current.ordinal == plan.milestones[-1].ordinal


def milestone_snapshot(plan: MilestonePlan) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": m.ordinal,
            "key": m.key,
            "title": m.title,
            "acceptance": m.acceptance,
            "is_final": m.is_final,
            "status": (
                "ACTIVE"
                if m.ordinal == plan.current_ordinal
                else ("ATTAINED" if m.ordinal < plan.current_ordinal else "PENDING")
            ),
        }
        for m in plan.milestones
    ]


def _should_rebuild_plan(metadata: dict[str, Any], existing: MilestonePlan) -> bool:
    """Rebuild fixed-template or underspecified LARGE plans."""
    if metadata.get("milestone_derivation") != _DERIVATION:
        return True
    if existing.goal_scale == GOAL_SCALE_LARGE and len(existing.milestones) < 2:
        return True
    # Old fixed keys from template era.
    keys = {m.key for m in existing.milestones}
    if keys >= {"m1-surface", "m2-content", "m3-criteria"}:
        return True
    return False


async def ensure_milestone_plan(
    session: AsyncSession,
    *,
    goal: GoalModel,
    spec: GoalSpecModel,
) -> MilestonePlan:
    """Idempotently attach a Goal-derived milestone plan."""
    metadata = dict(goal.metadata_json or {})
    existing = plan_from_metadata(metadata)
    if existing is not None and not _should_rebuild_plan(metadata, existing):
        return existing

    scale = classify_goal_scale(
        goal.original_input,
        dict(spec.success_criteria or {}),
        metadata,
        system_inferences=dict(spec.system_inferences or {}),
        unknowns=list(spec.unknowns or []),
    )
    first = str(
        metadata.get("first_deliverable")
        or (spec.success_criteria or {}).get("first_deliverable")
        or ""
    )
    milestones = propose_milestones(
        original_input=goal.original_input,
        success_criteria=dict(spec.success_criteria or {}),
        first_deliverable=first,
        goal_scale=scale,
        system_inferences=dict(spec.system_inferences or {}),
        unknowns=list(spec.unknowns or []),
        metadata=metadata,
    )
    if scale == GOAL_SCALE_LARGE and len(milestones) < 2:
        raise RuntimeError("LARGE goal must have at least 2 Goal-derived milestones")

    plan = MilestonePlan(scale, milestones, milestones[0].ordinal)
    metadata["goal_scale"] = scale
    metadata["milestone_derivation"] = _DERIVATION
    metadata["milestone_count"] = len(milestones)
    metadata["milestones"] = milestone_snapshot(plan)
    metadata["current_milestone_ordinal"] = plan.current_ordinal
    metadata["current_milestone_key"] = current_milestone(plan).key
    goal.metadata_json = metadata

    existing_keys = {
        str((w.metadata_json or {}).get("plan_key") or "")
        for w in await session.scalars(
            select(WorkModel).where(WorkModel.goal_id == goal.id)
        )
    }
    prev_id: uuid.UUID | None = None
    for spec_m in milestones:
        if spec_m.key in existing_keys:
            # Still track dependency chain for newly added ones.
            continue
        work_id = uuid.uuid4()
        session.add(
            WorkModel(
                id=work_id,
                goal_id=goal.id,
                purpose=spec_m.title,
                input_refs=[],
                acceptance_criteria=dict(spec_m.acceptance),
                dependency_ids=[str(prev_id)] if prev_id else [],
                priority=100 - spec_m.ordinal,
                budget={},
                status="PLANNED",
                version=0,
                correlation_id=goal.correlation_id,
                metadata_json={
                    "kind": _MILESTONE_KIND,
                    "plan_key": spec_m.key,
                    "ordinal": spec_m.ordinal,
                    "is_final": spec_m.is_final,
                    "derivation": _DERIVATION,
                    "required_capabilities": [],
                },
            )
        )
        prev_id = work_id
        existing_keys.add(spec_m.key)
    await session.flush()
    return plan


async def advance_milestone(
    session: AsyncSession,
    *,
    goal: GoalModel,
) -> MilestonePlan | None:
    """Mark current milestone ATTAINED and activate the next. None if already final."""
    metadata = dict(goal.metadata_json or {})
    plan = plan_from_metadata(metadata)
    if plan is None:
        return None
    if is_final_milestone(plan):
        return None
    nxt = None
    for item in plan.milestones:
        if item.ordinal > plan.current_ordinal:
            nxt = item
            break
    if nxt is None:
        return None
    new_plan = MilestonePlan(plan.goal_scale, plan.milestones, nxt.ordinal)
    metadata["current_milestone_ordinal"] = nxt.ordinal
    metadata["current_milestone_key"] = nxt.key
    metadata["milestones"] = milestone_snapshot(new_plan)
    metadata["last_attained_milestone_ordinal"] = plan.current_ordinal
    goal.metadata_json = metadata
    works = list(
        await session.scalars(select(WorkModel).where(WorkModel.goal_id == goal.id))
    )
    for work in works:
        meta = dict(work.metadata_json or {})
        if meta.get("kind") != _MILESTONE_KIND:
            continue
        if int(meta.get("ordinal") or 0) == plan.current_ordinal:
            work.status = "ACCEPTED"
        elif int(meta.get("ordinal") or 0) == nxt.ordinal:
            work.status = "READY"
    await session.flush()
    return new_plan


def acceptance_for_current_milestone(
    metadata: dict[str, Any],
    *,
    fallback_first_deliverable: str = "",
) -> dict[str, Any]:
    """Acceptance contract slice for the active milestone only."""
    plan = plan_from_metadata(metadata)
    if plan is None:
        out: dict[str, Any] = {}
        if fallback_first_deliverable:
            out["first_deliverable"] = fallback_first_deliverable
        return out
    current = current_milestone(plan)
    acceptance = dict(current.acceptance)
    acceptance["milestone_ordinal"] = current.ordinal
    acceptance["milestone_key"] = current.key
    acceptance["milestone_title"] = current.title
    acceptance["goal_scale"] = plan.goal_scale
    acceptance["milestone_count"] = len(plan.milestones)
    acceptance["forbid_full_goal_claim"] = (
        plan.goal_scale == GOAL_SCALE_LARGE and not current.is_final
    )
    return acceptance
