"""Recover delivery/goal-attainment gaps.

A0 Agent Loop exit (2026-08-03):
- VerificationGap / delivery gap → ``ASK_HUMAN`` or ``STOP`` (hard cap).
- **Forbidden**: silent auto ``SESSION_RESUME`` / lesson lottery / ATTRIBUTE_3 as brain.
- Same Session resume only after human answers (``resume_after_human``) or explicit CONTINUE.

Legacy: ATTRIBUTE_3 ladder remains fallback only when no Session and exit not enforced,
or when ``agent_loop_exit_enforced=False`` (ops kill-switch).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.capability_acquire_service import (
    AcquireRequest,
    AcquireResult,
    CapabilityAcquireService,
)
from regent.application.capability_build_service import build_attainment_capability
from regent.application.conversation_service import append_project_message
from regent.application.capability_ladder import (
    ATTAINMENT_LADDER_CYCLES,
    MAX_ATTAINMENT_ESCALATION_ATTEMPTS,
    EscalationStep,
    built_capability_name,
    composed_capability_name,
    plan_escalation,
)
from regent.application.delivery_state import (
    DeliveryState,
    gate_reorg_max,
    gate_reorg_step_name,
    recovery_budget_multiplier,
)
from regent.config import get_settings
from regent.application.capability_resolution_service import (
    CapabilityCandidate,
    CapabilityGap,
    CapabilityResolutionService,
    ResolutionMethod,
)
from regent.application.execution_events import (
    GENERATION_RUN_REQUESTED,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.live_action import merge_live_action_into_metadata
from regent.application.memory_service import AdmitMemory, MemoryKind, MemoryService
from regent.application.organization_service import OrganizationService
from regent.application.p1_contracts import canonical_hash
from regent.infrastructure.delivery_review_capability import (
    CAPABILITY_NAME as DELIVERY_REVIEW_NAME,
)
from regent.infrastructure.delivery_review_capability import (
    ensure_delivery_review_capability,
)
from regent.infrastructure.evidence_capability import (
    CAPABILITY_NAME as HTTP_SOURCE_NAME,
)
from regent.infrastructure.evidence_capability import (
    ensure_allowlisted_http_capability,
)
from regent.infrastructure.models import (
    CapabilityModel,
    CapabilityResolutionPlanModel,
    DiscoveryRoundModel,
    GenerationPlanModel,
    GoalModel,
    GoalSpecModel,
    ProductHypothesisModel,
    RequirementRevisionModel,
)
from regent.infrastructure.product_surface_capability import (
    CAPABILITY_NAME as PRODUCT_SURFACE_NAME,
)
from regent.infrastructure.product_surface_capability import (
    ensure_product_surface_capability,
    load_product_surface_capability_package,
)

logger = logging.getLogger(__name__)

_DELIVERY_POLICY = "goal_attainment_escalation"
_MAX_FAILURE_LESSONS = 8
_MAX_LEARNED_CONSTRAINTS = 16

_NAVIGATION_MARKERS = (
    "preview-internal-nav",
    "internal-nav",
    "broken-nav",
    "nav 404",
    "detail/nav",
)
_PRESENTATION_MARKERS = (
    "stylesheet-present",
    "stylesheet-substance",
    "styled-surface",
    "stylesheet",
    "product-structure",
    "forbid-demo-shell",
    "semantic-main",
    "preview-asset-reachability",
    "preview-home-reachable",
    "preview-product-qa",
)
_EVIDENCE_MARKERS = (
    "observed-entries-rendered",
    "goal-outbound-links",
)
_GOAL_INTENT_MARKERS = (
    "goal-first-deliverable",
    "first-deliverable",
    "required-phrases",
    "min-list-items",
    "min-visible-text",
)

_KIND_GUIDANCE: dict[str, tuple[str, ...]] = {
    "navigation": (
        "Fix broken in-app navigation first: every linked detail/crosswalk path must return HTTP <400 HTML.",
        "Do not chase CSS/typography when the failing check is preview-internal-nav — repair routes and hrefs.",
        "Align template hrefs with Flask routes (case, trailing slash, slug); seed data must match linked IDs.",
    ),
    "presentation": (
        "Fix presentation first: add substantial CSS (stylesheet + layout + typography).",
        "Never ship browser-default dumps; use semantic <main> and designed product structure.",
    ),
    "evidence": (
        "Render observed evidence as primary content with source labels and real https outbound links.",
        "If evidence is missing, Core will escalate allowlisted-http-source-v1; do not invent dead stubs.",
    ),
    "goal_intent": (
        "Satisfy GoalSpec first_deliverable and success_criteria keywords on the visible page.",
        "Match required phrases / list density from the Goal; do not ignore the stated deliverable.",
    ),
    "product_surface": (
        "Validate against GoalSpec success_criteria and first_deliverable before claiming done.",
        "Ship a designed product UI; fail closed rather than publish an unreliable surface.",
    ),
    "gate_failed": (
        "Previous preview gate failed; rebuild toward Goal attainment with stronger capability binding.",
        "Prefer observed evidence and success_criteria over generator self-score.",
    ),
}


@dataclass(frozen=True, slots=True)
class DeliveryGapRecoveryResult:
    recovered: bool
    method: str
    message: str
    attempts: int
    gap_kind: str = "product_surface"
    terminal_exhaust: bool = False
    recovery_work_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


def classify_delivery_gap_kind(gap_reasons: list[str]) -> str:
    """Map delivery-review failure codes to recovery routing kind."""
    joined = " ".join(str(r).lower() for r in gap_reasons if str(r).strip())
    if not joined:
        return "product_surface"
    if "user_abort" in joined:
        return "USER_ABORT"
    if "tool_permission" in joined:
        return "TOOL_PERMISSION"
    if "ask_user_required" in joined or "ask_user:" in joined:
        return "ASK_USER"
    if "plan_approve" in joined:
        return "PLAN_APPROVE"
    if "budget_exhausted" in joined:
        return "BUDGET_EXHAUSTED"
    # Nav/404 must win over presentation — otherwise agents keep polishing CSS
    # while the same broken href loops forever.
    if any(m in joined for m in _NAVIGATION_MARKERS):
        return "navigation"
    if any(m in joined for m in _PRESENTATION_MARKERS):
        return "presentation"
    if any(m in joined for m in _EVIDENCE_MARKERS):
        return "evidence"
    if any(m in joined for m in _GOAL_INTENT_MARKERS):
        return "goal_intent"
    return "product_surface"


def guidance_for_gap_kind(gap_kind: str) -> tuple[str, ...]:
    kind_lines = _KIND_GUIDANCE.get(gap_kind) or _KIND_GUIDANCE["product_surface"]
    package = load_product_surface_capability_package()
    merged: list[str] = list(kind_lines)
    for line in package.generation_guidance:
        if line not in merged:
            merged.append(line)
    return tuple(merged[:8])


def build_learned_constraints(gap_kind: str, gap_reasons: list[str]) -> list[str]:
    """Turn failure codes into concrete do-not / must-fix constraints for replanning."""
    from urllib.parse import urlparse

    constraints: list[str] = [
        f"Do not repeat the prior rejected surface for gap_kind={gap_kind}.",
        "Absorb prior failure lessons before emitting another deliverable.",
    ]
    joined = " ".join(r.lower() for r in gap_reasons)
    if gap_kind == "navigation" or "preview-internal-nav" in joined or " → 404" in joined:
        constraints.append(
            "MUST fix broken in-app links before cosmetics: every href probed by "
            "preview-internal-nav must return HTML 2xx (no 404)."
        )
        constraints.append(
            "Align routes with linked paths (case-insensitive slug ok); seed entities "
            "referenced by nav must exist."
        )
        if "not found" in joined or "crosswalk not found" in joined:
            constraints.append(
                "If the route exists but returns not-found, fix data lookup keys/seed "
                "(dict key mismatch is a common root cause), not CSS."
            )
        for reason in gap_reasons:
            for token in str(reason).replace(";", " ").split():
                if "/crosswalks/" in token or "/countries/" in token or "/item/" in token:
                    path = token.split("→")[0].split("->")[0].strip().rstrip(",")
                    if path.startswith("http"):
                        path = urlparse(path).path or path
                    item = f"MUST make this path return HTML 200: {path}"
                    if item not in constraints:
                        constraints.append(item)
    if "stylesheet" in joined or "styled-surface" in joined or (
        gap_kind == "presentation" and "preview-internal-nav" not in joined
    ):
        constraints.append(
            "Must ship substantial CSS (layout + typography); no browser-default dumps."
        )
    if "outbound" in joined or "observed" in joined or gap_kind == "evidence":
        constraints.append(
            "Must render observed evidence with real https outbound links and source labels."
        )
    if "first-deliverable" in joined or "required-phrases" in joined or gap_kind == "goal_intent":
        constraints.append(
            "Must satisfy GoalSpec first_deliverable / success_criteria keywords on the page."
        )
    if "deployment" in joined or "deploy" in joined:
        constraints.append(
            "Prior preview deploy failed (GAC-A4); regenerate a deployable, review-passing surface."
        )
    if "invalid-state" in joined or "frozen generation plan" in joined:
        constraints.append(
            "Prior generation hit INVALID_STATE; replan with changed inputs — do not reuse the dead plan digest blindly."
        )
    if "placeholder" in joined or "demo-shell" in joined or "forbid-demo" in joined:
        constraints.append("Forbid placeholder/demo-shell content; ship goal-aligned product UI.")
    for reason in gap_reasons[:6]:
        item = f"Fix: {reason}"
        if item not in constraints:
            constraints.append(item)
    return constraints[:_MAX_LEARNED_CONSTRAINTS]


def build_failure_lesson(
    *,
    gap_reasons: list[str],
    gap_kind: str,
    method: str,
    attempt: int,
    halt_context: dict[str, Any] | None = None,
    goal_text: str = "",
) -> dict[str, Any]:
    """Structured lesson persisted for the next generation round."""
    halt = dict(halt_context or {})
    reasons = list(gap_reasons)[:12]
    constraints = build_learned_constraints(gap_kind, gap_reasons)
    halt_message = str(halt.get("message") or "")[:400]
    last_error = str(halt.get("last_error") or halt.get("error") or "")[:400]
    summary_bits = [str(r) for r in reasons[:3] if str(r).strip()]
    if not summary_bits and last_error:
        summary_bits = [last_error]
    if not summary_bits and halt_message:
        summary_bits = [halt_message]
    summary = "; ".join(summary_bits)[:400] or f"delivery gap: {gap_kind}"
    avoid = (
        "; ".join(str(c) for c in constraints[:4] if str(c).strip())[:400]
        or "下次须避开本轮 gap_reasons，并满足 learned_constraints"
    )
    lesson = {
        "at": datetime.now(UTC).isoformat(),
        "attempt": attempt,
        "gap_kind": gap_kind,
        "escalation_method": method,
        "gap_reasons": reasons,
        "learned_constraints": constraints,
        "halt_stage": str(halt.get("stage") or halt.get("execution_stage") or ""),
        "halt_message": halt_message,
        "last_error": last_error,
        "goal_text": goal_text[:240],
        "replan_required": True,
        # Dual-write: same fields as append_failure_lesson for acceptance readers.
        "summary": summary,
        "avoid": avoid,
        "code": f"DELIVERY_GAP_{str(gap_kind).upper()}"[:128],
    }
    lesson["lesson_digest"] = canonical_hash(
        {
            "attempt": attempt,
            "gap_kind": gap_kind,
            "method": method,
            "reasons": lesson["gap_reasons"],
            "constraints": lesson["learned_constraints"],
            "last_error": lesson["last_error"],
        }
    )[:24]
    return lesson


class DeliveryGapRecoveryService:
    _append = staticmethod(append_project_message)
    """Escalate capabilities + reorganize agents when delivery does not attain Goal."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._resolver = CapabilityResolutionService()
        self._orgs = OrganizationService(sessions)
        self._memories = MemoryService(sessions)

    async def recover(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        requirement_revision_id: uuid.UUID,
        capability_resolution_plan_id: uuid.UUID,
        actor: str,
        gap_reasons: list[str],
        halt_context: dict[str, Any] | None = None,
        org_key: str = "default",
    ) -> DeliveryGapRecoveryResult:
        surface_id = await ensure_product_surface_capability(self._sessions)
        review_id = await ensure_delivery_review_capability(self._sessions)
        http_id = await ensure_allowlisted_http_capability(self._sessions)
        reasons = [str(r) for r in gap_reasons if str(r).strip()][:12]
        gap_kind = classify_delivery_gap_kind(reasons)
        guidance = guidance_for_gap_kind(gap_kind)

        lesson_for_memory: dict[str, Any] | None = None
        result: DeliveryGapRecoveryResult | None = None
        pending_acquire: AcquireRequest | None = None
        pending_plan_attempt: int | None = None
        pending_merged_halt: dict[str, Any] | None = None
        pending_persona = "balanced"

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return DeliveryGapRecoveryResult(
                    False, "BLOCK", "goal not found", 0, gap_kind
                )
            await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            metadata = dict(goal.metadata_json or {})
            # Keep concrete live-preview failures sticky across later deploy/domain
            # errors so recovery does not forget the real product gap (e.g. 404 nav).
            sticky_qa = [
                str(x).strip()
                for x in list(metadata.get("live_preview_qa_failures") or [])
                + list((halt_context or {}).get("live_preview_qa_failures") or [])
                if str(x).strip()
            ]
            if sticky_qa:
                merged_reasons: list[str] = []
                for item in list(reasons) + [
                    (
                        r
                        if r.startswith("PREVIEW_PRODUCT_QA_FAILED:")
                        else f"PREVIEW_PRODUCT_QA_FAILED: {r}"
                    )
                    for r in sticky_qa
                ]:
                    if item and item not in merged_reasons:
                        merged_reasons.append(item)
                reasons = merged_reasons[:12]
                gap_kind = classify_delivery_gap_kind(reasons)
                guidance = guidance_for_gap_kind(gap_kind)
            # Respect an existing soft-pause (ops or runtime). Do not let in-flight
            # DeliveryStateChanged / gap recovery overwrite DELIVERY_SOFT_PAUSE with
            # another GENERATING replan — that is the high-burn escape hatch.
            stage_now = str(metadata.get("execution_stage") or "")
            if stage_now == "DELIVERY_SOFT_PAUSE" or metadata.get("ops_soft_pause"):
                # Exception: a fresh preview QA failure with concrete sticky gaps must
                # still be absorbable after human/ops CONTINUE cleared the pause path.
                # When caller is PREVIEW_PRODUCT_QA_FAILED, allow recovery to proceed.
                halt_stage = str((halt_context or {}).get("stage") or "")
                if halt_stage != "PREVIEW_PRODUCT_QA_FAILED":
                    # HUMAN_TASK_REQUIRED: soft-pause hands off to human intervention.
                    return DeliveryGapRecoveryResult(
                        False,
                        "SOFT_PAUSE",
                        "goal already soft-paused; refusing further auto gap recovery",
                        int(metadata.get("delivery_gap_recovery_attempts") or 0),
                        gap_kind,
                        terminal_exhaust=True,
                    )
            # Merge halt already on the goal with caller-supplied context.
            prior_halt = dict(metadata.get("halt") or {})
            merged_halt = {**prior_halt, **dict(halt_context or {})}
            # Persist last-good draft for human handoff / preview surfacing.
            draft_uri = str(
                merged_halt.get("draft_uri")
                or metadata.get("last_good_draft_uri")
                or ""
            ).strip()
            if draft_uri:
                metadata["last_good_draft_uri"] = draft_uri
            attempts = int(metadata.get("delivery_gap_recovery_attempts") or 0)

            # Same gap_kind hard cap: auto-reset a few times, then soft-pause (no TaskCard).
            # Delivery gaps are not permission/danger — never mint「总是允许」卡。
            from regent.application.delivery_success_policy import (
                DELIVERY_GAP_AUTO_CONTINUE_MAX,
                DELIVERY_GAP_TOTAL_ATTEMPTS_HARD_CAP,
                SAME_GAP_KIND_HARD_CAP,
            )

            # Sticky total across gap_kind flips / auto-continue resets.
            total_attempts = int(metadata.get("delivery_gap_total_attempts") or 0) + 1
            metadata["delivery_gap_total_attempts"] = total_attempts
            if total_attempts >= DELIVERY_GAP_TOTAL_ATTEMPTS_HARD_CAP:
                draft_note = f" 当前草稿：{draft_uri}" if draft_uri else ""
                message = (
                    f"交付缺口已累计自动修复 {total_attempts} 次仍未过关"
                    f"（当前 gap={gap_kind}）。"
                    "已暂停自动升级；可在对话补充方向继续，无需点「总是允许」。"
                    f"{draft_note}"
                )
                return await self._soft_pause_delivery(
                    session,
                    goal=goal,
                    project_id=project_id,
                    metadata=metadata,
                    gap_kind=gap_kind,
                    reasons=reasons,
                    attempts=total_attempts,
                    message=message,
                    summary=(
                        "同一目标交付缺口多次自动修复仍未过关，已暂停自动升级。"
                        "可在对话补充方向后继续。"
                    ),
                    extra_termination={
                        "total_attempts_cap": True,
                        "gap_kind": gap_kind,
                        "draft_uri": draft_uri,
                    },
                )

            # A0: gap → ASK_HUMAN (or STOP on hard cap). Do NOT auto SESSION_RESUME.
            # Same-session continue only after human answers (resume_after_human).
            exit_enforced = bool(
                getattr(get_settings(), "agent_loop_exit_enforced", True)
            )
            if exit_enforced:
                from regent.application.agent_loop_exit import detect_doom_loop
                from regent.application.progress_roi import (
                    compute_workspace_hash,
                    _workspace_root_from_metadata,
                )

                # H0: user abort is always STOP (not another ASK).
                if gap_kind == "USER_ABORT":
                    return await self._exit_stop_or_ask(
                        session,
                        goal=goal,
                        project_id=project_id,
                        metadata=metadata,
                        gap_kind=gap_kind,
                        reasons=reasons,
                        attempts=total_attempts,
                        draft_uri=draft_uri or None,
                        exit_kind="STOP",
                        stop_reason="user_abort",
                        actor=actor,
                    )

                ws_root = _workspace_root_from_metadata(metadata)
                ws_hash = compute_workspace_hash(ws_root)
                is_doom, doom_reason = detect_doom_loop(
                    metadata, gap_kind=gap_kind, workspace_hash=ws_hash
                )
                # Update streak for doom tracking even when asking.
                prior_kind = str(metadata.get("delivery_gap_kind") or "")
                streak = int(metadata.get("delivery_gap_kind_streak") or 0)
                metadata["delivery_gap_kind"] = gap_kind
                metadata["delivery_gap_kind_streak"] = (
                    streak + 1 if prior_kind == gap_kind else 1
                )
                if is_doom or total_attempts >= DELIVERY_GAP_TOTAL_ATTEMPTS_HARD_CAP:
                    stop_reason = doom_reason or "hard_cap"
                    return await self._exit_stop_or_ask(
                        session,
                        goal=goal,
                        project_id=project_id,
                        metadata=metadata,
                        gap_kind=gap_kind,
                        reasons=reasons,
                        attempts=total_attempts,
                        draft_uri=draft_uri or None,
                        exit_kind="STOP" if total_attempts >= DELIVERY_GAP_TOTAL_ATTEMPTS_HARD_CAP else "ASK_HUMAN",
                        stop_reason=stop_reason if is_doom else "total_attempts_hard_cap",
                        actor=actor,
                    )
                return await self._exit_stop_or_ask(
                    session,
                    goal=goal,
                    project_id=project_id,
                    metadata=metadata,
                    gap_kind=gap_kind,
                    reasons=reasons,
                    attempts=total_attempts,
                    draft_uri=draft_uri or None,
                    exit_kind="ASK_HUMAN",
                    stop_reason="verification_gap",
                    actor=actor,
                )

            # Legacy kill-switch path: auto SESSION_RESUME when exit not enforced.
            if bool(getattr(get_settings(), "agent_session_resume_enabled", True)) and metadata.get(
                "project_agent_session_id"
            ):
                session_resume = await self._resume_same_agent_session(
                    session,
                    goal=goal,
                    project_id=project_id,
                    requirement_revision_id=requirement_revision_id,
                    capability_resolution_plan_id=capability_resolution_plan_id,
                    actor=actor,
                    reasons=reasons,
                    gap_kind=gap_kind,
                    metadata=metadata,
                    merged_halt=merged_halt,
                    total_attempts=total_attempts,
                )
                if session_resume is not None:
                    return session_resume

            prior_kind = str(metadata.get("delivery_gap_kind") or "")
            streak = int(metadata.get("delivery_gap_kind_streak") or 0)
            if prior_kind == gap_kind:
                streak += 1
            else:
                streak = 1
            metadata["delivery_gap_kind"] = gap_kind
            metadata["delivery_gap_kind_streak"] = streak
            if streak >= SAME_GAP_KIND_HARD_CAP:
                auto_cycles = int(metadata.get("delivery_gap_auto_continue_cycles") or 0)
                if auto_cycles < DELIVERY_GAP_AUTO_CONTINUE_MAX:
                    metadata["delivery_gap_auto_continue_cycles"] = auto_cycles + 1
                    metadata["delivery_gap_kind_streak"] = 0
                    streak = 0
                    # Keep delivery_gap_recovery_attempts / total_attempts —
                    # resetting attempts enabled infinite burn across kind flips.
                    logger.info(
                        "delivery gap hard-cap auto-continue",
                        extra={
                            "goal_id": str(goal.id),
                            "gap_kind": gap_kind,
                            "auto_cycle": auto_cycles + 1,
                            "total_attempts": total_attempts,
                        },
                    )
                else:
                    draft_note = f" 当前草稿：{draft_uri}" if draft_uri else ""
                    message = (
                        f"同一类交付缺口（{gap_kind}）已连续自动修复仍未过关。"
                        "已暂停自动升级；可在对话补充方向继续，无需点「总是允许」。"
                        f"{draft_note}"
                    )
                    return await self._soft_pause_delivery(
                        session,
                        goal=goal,
                        project_id=project_id,
                        metadata=metadata,
                        gap_kind=gap_kind,
                        reasons=reasons,
                        attempts=attempts,
                        message=message,
                        summary=f"同类缺口已达自动修复上限（{gap_kind}）",
                        extra_termination={
                            "same_gap_kind_cap": True,
                            "gap_kind_streak": streak,
                            "draft_uri": draft_uri or None,
                        },
                    )

            # navigation / goal_intent / presentation / evidence 都是正常交付修复：
            # 一律走能力阶梯自动重试；耗尽后自动再开几轮，再不行才软暂停（无确认卡）。

            # AC5: persona scales the auto-recovery ladder. balanced -> unchanged.
            # CD-7.3: delivery_profile is the authority for recovery budgets.
            _persona = getattr(get_settings(), "delivery_profile", "balanced")
            pending_persona = str(_persona)
            _effective_max = int(
                round(MAX_ATTAINMENT_ESCALATION_ATTEMPTS * recovery_budget_multiplier(_persona))
            )
            plan = plan_escalation(attempts, max_attempts=_effective_max)

            if plan.exhausted or plan.step is EscalationStep.STOP:
                auto_cycles = int(metadata.get("delivery_gap_auto_continue_cycles") or 0)
                if auto_cycles < DELIVERY_GAP_AUTO_CONTINUE_MAX:
                    attempts = 0
                    metadata["delivery_gap_recovery_attempts"] = 0
                    metadata["delivery_gap_kind_streak"] = 0
                    metadata["delivery_gap_auto_continue_cycles"] = auto_cycles + 1
                    plan = plan_escalation(0, max_attempts=_effective_max)
                    logger.info(
                        "delivery gap ladder-exhaust auto-continue",
                        extra={
                            "goal_id": str(goal.id),
                            "gap_kind": gap_kind,
                            "auto_cycle": auto_cycles + 1,
                        },
                    )
                if plan.exhausted or plan.step is EscalationStep.STOP:
                    message = (
                        "交付仍未达成 Goal。已穷举 ATTRIBUTE_3 能力阶梯 "
                        f"（REUSE→CONFIGURE→COMPOSE→BUILD→ACQUIRE ×{ATTAINMENT_LADDER_CYCLES} 轮，"
                        f"共 {_effective_max} 次）并已自动续跑。"
                        "拒绝发布不可靠表面；可在对话补充方向继续，无需点「总是允许」。"
                    )
                    return await self._soft_pause_delivery(
                        session,
                        goal=goal,
                        project_id=project_id,
                        metadata=metadata,
                        gap_kind=gap_kind,
                        reasons=reasons,
                        attempts=attempts,
                        message=message,
                        summary="自动修复已用尽，可在对话补充方向",
                        extra_termination={"ladder_exhausted": True},
                    )

            # CD-7.2: ACQUIRE network I/O must leave the write transaction.
            if plan.step is EscalationStep.ACQUIRE:
                pending_acquire = AcquireRequest(
                    capability_name=f"acquired-{gap_kind}-v1",
                    requirement_key=f"delivery.acquire.{gap_kind}",
                    goal_id=goal_id,
                    actor_id=actor,
                )
                pending_plan_attempt = plan.attempt
                pending_merged_halt = merged_halt
            else:
                candidates = await self._load_candidates(
                    session,
                    surface_id=surface_id,
                    review_id=review_id,
                    http_id=http_id,
                )
                method, primary_name, primary_id, extra_guidance = await self._apply_step(
                    session,
                    goal_id=goal_id,
                    step=plan.step,
                    gap_kind=gap_kind,
                    guidance=guidance,
                    reasons=reasons,
                    candidates=candidates,
                    surface_id=surface_id,
                    http_id=http_id,
                    review_id=review_id,
                )
                result = await self._commit_recovery_escalation(
                    session,
                    goal=goal,
                    project_id=project_id,
                    requirement_revision_id=requirement_revision_id,
                    capability_resolution_plan_id=capability_resolution_plan_id,
                    actor=actor,
                    gap_kind=gap_kind,
                    reasons=reasons,
                    guidance=guidance,
                    merged_halt=merged_halt,
                    plan_attempt=plan.attempt,
                    plan_step=plan.step,
                    method=method,
                    primary_name=primary_name,
                    primary_id=primary_id,
                    extra_guidance=extra_guidance,
                    surface_id=surface_id,
                    http_id=http_id,
                    review_id=review_id,
                    effective_max=_effective_max,
                )
                lessons = list((goal.metadata_json or {}).get("failure_lessons") or [])
                lesson_for_memory = lessons[-1] if lessons else None

        # CD-7.2 phase B: network acquire outside any open begin().
        if pending_acquire is not None and pending_plan_attempt is not None:
            acquire_result = await CapabilityAcquireService(self._sessions).acquire(
                pending_acquire
            )
            _effective_max = int(
                round(
                    MAX_ATTAINMENT_ESCALATION_ATTEMPTS
                    * recovery_budget_multiplier(pending_persona)
                )
            )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id, with_for_update=True)
                if goal is None:
                    return DeliveryGapRecoveryResult(
                        False, "BLOCK", "goal not found", 0, gap_kind
                    )
                metadata = dict(goal.metadata_json or {})
                attempts = int(metadata.get("delivery_gap_recovery_attempts") or 0)
                plan = plan_escalation(attempts, max_attempts=_effective_max)
                if plan.step is not EscalationStep.ACQUIRE or plan.attempt != pending_plan_attempt:
                    return DeliveryGapRecoveryResult(
                        False,
                        "BLOCK",
                        "concurrent recovery changed ladder step; acquire discarded",
                        attempts,
                        gap_kind,
                    )
                candidates = await self._load_candidates(
                    session,
                    surface_id=surface_id,
                    review_id=review_id,
                    http_id=http_id,
                )
                method, primary_name, primary_id, extra_guidance = await self._apply_step(
                    session,
                    goal_id=goal_id,
                    step=EscalationStep.ACQUIRE,
                    gap_kind=gap_kind,
                    guidance=guidance,
                    reasons=reasons,
                    candidates=candidates,
                    surface_id=surface_id,
                    http_id=http_id,
                    review_id=review_id,
                    acquire_result=acquire_result,
                )
                result = await self._commit_recovery_escalation(
                    session,
                    goal=goal,
                    project_id=project_id,
                    requirement_revision_id=requirement_revision_id,
                    capability_resolution_plan_id=capability_resolution_plan_id,
                    actor=actor,
                    gap_kind=gap_kind,
                    reasons=reasons,
                    guidance=guidance,
                    merged_halt=dict(pending_merged_halt or {}),
                    plan_attempt=plan.attempt,
                    plan_step=plan.step,
                    method=method,
                    primary_name=primary_name,
                    primary_id=primary_id,
                    extra_guidance=extra_guidance,
                    surface_id=surface_id,
                    http_id=http_id,
                    review_id=review_id,
                    effective_max=_effective_max,
                )
                lessons = list((goal.metadata_json or {}).get("failure_lessons") or [])
                lesson_for_memory = lessons[-1] if lessons else None

        if result is not None and result.recovered and lesson_for_memory is not None:
            await self._admit_failure_memories(
                org_key=org_key,
                goal_id=goal_id,
                project_id=project_id,
                actor=actor,
                lesson=lesson_for_memory,
            )
        assert result is not None
        return result

    async def _exit_stop_or_ask(
        self,
        session: AsyncSession,
        *,
        goal: GoalModel,
        project_id: uuid.UUID,
        metadata: dict[str, Any],
        gap_kind: str,
        reasons: list[str],
        attempts: int,
        draft_uri: str | None,
        exit_kind: str,
        stop_reason: str,
        actor: str,
    ) -> DeliveryGapRecoveryResult:
        """A0: persist COMPLETE/STOP/ASK_HUMAN and stop auto-burn."""
        from regent.application.agent_loop_exit import (
            apply_exit_to_metadata,
            build_ask_envelope,
            build_exit,
            conversation_copy_for_exit,
        )
        from regent.application.project_agent_session import ProjectAgentSessionService

        session_id = metadata.get("project_agent_session_id")
        epoch = metadata.get("project_agent_session_epoch")
        ask = None
        if exit_kind == "ASK_HUMAN":
            if gap_kind == "PLAN_APPROVE":
                from regent.application.work_plan import plan_approve_envelope

                plan_items = []
                for r in reasons:
                    text = str(r)
                    if text.startswith("plan:"):
                        parts = text.split(":", 2)
                        if len(parts) >= 3:
                            plan_items.append({"id": parts[1], "content": parts[2]})
                ask = plan_approve_envelope(items=plan_items or [{"content": r} for r in reasons[:6]])
            elif gap_kind == "TOOL_PERMISSION":
                from regent.application.agent_control import permission_ask_envelope

                tool_name = "tool"
                preview = ""
                for r in reasons:
                    text = str(r)
                    if text.startswith("TOOL_PERMISSION_REQUIRED:"):
                        tool_name = text.split(":", 1)[-1].strip() or tool_name
                    if text.startswith("preview:"):
                        preview = text.split(":", 1)[-1].strip()
                ask = permission_ask_envelope(tool_name=tool_name, args_preview=preview)
            elif gap_kind == "ASK_USER":
                question = "Agent 需要你确认后再继续。"
                ask_type = "ask_user"
                blocked_item: str | None = None
                for r in reasons:
                    text = str(r)
                    if text.startswith("ASK_USER_REQUIRED:"):
                        question = text.split(":", 1)[-1].strip() or question
                    elif text.startswith("ask_type:"):
                        ask_type = text.split(":", 1)[-1].strip() or ask_type
                    elif text.startswith("blocked_item:"):
                        blocked_item = text.split(":", 1)[-1].strip() or None
                ask = build_ask_envelope(
                    question=question[:800],
                    why_blocked=(
                        "同一步无进展，需要你改方向或确认。"
                        if ask_type == "progress_loop"
                        else "Agent 调用了 ask_user_question。"
                    ),
                    ask_type=ask_type,
                    gap_kind="PROGRESS_LOOP" if ask_type == "progress_loop" else "ASK_USER",
                    blocked_item_key=blocked_item,
                )
                if blocked_item and "卡在清单项" not in str(ask.get("question") or ""):
                    ask["question"] = (
                        f"{ask.get('question') or ''}\n（卡在清单项: {blocked_item}）"
                    )[:800]
            else:
                why = (
                    f"交付验证未通过（{gap_kind}）。"
                    if stop_reason == "verification_gap"
                    else f"检测到无进展循环（{stop_reason}）。"
                )
                ask = build_ask_envelope(
                    question=(
                        "本轮未能完成交付。请选择下一步，或补充修改方向后发送「继续」。"
                    ),
                    why_blocked=why,
                    gap_kind=gap_kind,
                    gap_reasons=reasons,
                    ask_type="doom_loop" if stop_reason.startswith("doom_loop") else "delivery_gap",
                )
            # Progress ROI: stamp evaluation table + rewrite options when stagnant.
            try:
                from regent.application.progress_roi import (
                    apply_roi_on_exit,
                    build_progress_snapshot,
                    compute_workspace_hash,
                    enrich_ask_with_roi,
                    load_ledger_from_workspace,
                    _workspace_root_from_metadata,
                )

                settings = get_settings()
                roi_enforced = bool(getattr(settings, "progress_roi_enforced", True))
                ws_root = _workspace_root_from_metadata(metadata)
                ws_hash = compute_workspace_hash(ws_root)
                ledger = load_ledger_from_workspace(ws_root)
                snap = build_progress_snapshot(
                    metadata,
                    workspace_hash=ws_hash,
                    ledger=ledger,
                    gap_reasons=reasons,
                    gap_kind=gap_kind,
                )
                metadata, roi_state = apply_roi_on_exit(
                    metadata,
                    snapshot=snap,
                    min_tokens=int(getattr(settings, "progress_roi_min_tokens", 2000) or 2000),
                    stagnant_stop=int(
                        getattr(settings, "progress_roi_stagnant_stop", 3) or 3
                    ),
                    enforced=roi_enforced,
                )
                if ask is not None:
                    ask_type_now = str(ask.get("ask_type") or "")
                    if ask_type_now in {
                        "delivery_gap",
                        "doom_loop",
                        "progress_roi",
                        "progress_loop",
                        "",
                    }:
                        ask = enrich_ask_with_roi(ask, roi_state, enforced=roi_enforced)
                # ROI stop ladder may escalate ASK → STOP when streak exhausted.
                if (
                    roi_enforced
                    and exit_kind == "ASK_HUMAN"
                    and str(roi_state.get("next_action") or "") == "stop"
                    and int(roi_state.get("stagnant_streak") or 0)
                    >= int(getattr(settings, "progress_roi_stagnant_stop", 3) or 3)
                ):
                    exit_kind = "STOP"
                    stop_reason = (
                        f"doom_loop:roi_no_progress:streak={roi_state.get('stagnant_streak')}"
                    )
                    ask = None
            except Exception:
                logger.warning(
                    "progress_roi exit stamp failed",
                    extra={"goal_id": str(goal.id)},
                    exc_info=True,
                )
            # H1.5: surface which plan item is blocking.
            try:
                from regent.application.execution_plan import ExecutionPlanService
                from regent.application.work_plan import current_blocked_item_key

                plan_views = await ExecutionPlanService(self._sessions).list_items(goal.id)
                blocked = current_blocked_item_key([i.as_dict() for i in plan_views])
                if ask is not None and blocked:
                    ask = dict(ask)
                    ask["blocked_item_key"] = blocked
                    ask["question"] = (
                        f"{ask.get('question') or ''}\n（卡在清单项: {blocked}）"
                    )[:800]
            except Exception:
                pass
        exit_payload = build_exit(
            exit_kind=exit_kind,  # type: ignore[arg-type]
            stop_reason=stop_reason,
            lease_id=metadata.get("last_generation_run_id"),
            session_id=session_id,
            epoch=int(epoch) if epoch is not None else None,
            ask_envelope=ask,
            draft_uri=draft_uri,
        )
        metadata = apply_exit_to_metadata(metadata, exit_payload)
        metadata["delivery_gap_reasons"] = reasons
        metadata["delivery_gap_kind"] = gap_kind
        metadata["ops_soft_pause"] = {
            "at": datetime.now(UTC).isoformat(),
            "reason": f"agent_loop_exit:{exit_kind}:{stop_reason}",
            "gap_kind": gap_kind,
            "attempts": attempts,
        }
        msg_type, content = conversation_copy_for_exit(exit_payload)
        metadata = merge_live_action_into_metadata(
            metadata,
            content.split("\n")[0][:120],
            stage="DELIVERY_SOFT_PAUSE",
            event_type=msg_type,
        )
        # Drop busy live spinner — waiting on human or stopped.
        if exit_kind in {"ASK_HUMAN", "STOP"}:
            # keep live_action from merge above (exit summary)
            pass
        goal.metadata_json = metadata
        flag_modified(goal, "metadata_json")

        # Pause Session chassis so require_active fails until resume_from_paused.
        try:
            sessions_svc = ProjectAgentSessionService(self._sessions)
            active = await sessions_svc.get_active_in(session, project_id)
            if active is not None:
                row = await sessions_svc._require_active_row(session, project_id)  # noqa: SLF001
                from regent.application.project_agent_session import SESSION_STATUS_PAUSED

                row.status = SESSION_STATUS_PAUSED
                row.version = int(row.version or 0) + 1
                ckpt = dict(row.checkpoint_json or {})
                ckpt["last_exit"] = exit_payload
                row.checkpoint_json = ckpt
        except Exception:
            logger.warning(
                "failed to pause ProjectAgentSession on loop exit",
                extra={"goal_id": str(goal.id)},
                exc_info=True,
            )

        await self._append(
            session,
            project_id,
            role="ASSISTANT",
            message_type=msg_type,
            content=content,
            metadata={
                "goal_id": str(goal.id),
                "app_project_id": str(project_id),
                "agent_loop_exit": exit_payload,
                "actor": actor,
            },
        )

        # Optional HumanTask for console (not DELIVERY_GAP_INTERVENE / 总是允许).
        if exit_kind == "ASK_HUMAN":
            try:
                from datetime import timedelta

                from regent.infrastructure.models import HumanTaskModel

                session.add(
                    HumanTaskModel(
                        id=uuid.uuid4(),
                        goal_id=goal.id,
                        work_id=None,
                        run_id=None,
                        task_type="AGENT_LOOP_ASK",
                        prompt=str((ask or {}).get("question") or content)[:500],
                        requested_by=actor or "regent-core",
                        due_at=datetime.now(UTC) + timedelta(days=7),
                        status="OPEN",
                    )
                )
            except Exception:
                logger.warning(
                    "AGENT_LOOP_ASK human task create failed (conversation still has ask)",
                    extra={"goal_id": str(goal.id)},
                    exc_info=True,
                )

        method = "ASK_HUMAN" if exit_kind == "ASK_HUMAN" else "STOP"
        # HUMAN_TASK_REQUIRED: exit routes to human handoff (ASK_HUMAN) or hard STOP.
        return DeliveryGapRecoveryResult(
            False,
            method,
            content[:500],
            attempts,
            gap_kind,
            terminal_exhaust=True,
        )

    async def _resume_same_agent_session(
        self,
        session: AsyncSession,
        *,
        goal: GoalModel,
        project_id: uuid.UUID,
        requirement_revision_id: uuid.UUID,
        capability_resolution_plan_id: uuid.UUID,
        actor: str,
        reasons: list[str],
        gap_kind: str,
        metadata: dict[str, Any],
        merged_halt: dict[str, Any],
        total_attempts: int,
    ) -> DeliveryGapRecoveryResult | None:
        """Resume existing ProjectAgentSession — no ATTRIBUTE_3 / org reorg.

        Returns None when no ACTIVE session exists so the ladder remains fallback.
        """
        from regent.application.project_agent_session import ProjectAgentSessionService

        sessions_svc = ProjectAgentSessionService(self._sessions)
        active = await sessions_svc.get_active_in(session, project_id)
        if active is None:
            # No chassis yet → keep ATTRIBUTE_3 ladder as fallback (legacy / tests).
            return None

        bumped = await sessions_svc.bump_epoch_in(
            session,
            project_id,
            checkpoint_patch={
                "last_gap_kind": gap_kind,
                "last_gap_reasons": reasons[:12],
                "last_halt": {
                    k: merged_halt[k]
                    for k in ("draft_uri", "error_code", "summary")
                    if k in merged_halt
                },
                "resume_method": "SESSION_RESUME",
            },
        )
        lesson = build_failure_lesson(
            gap_reasons=reasons,
            gap_kind=gap_kind,
            method="SESSION_RESUME",
            attempt=total_attempts,
            halt_context=merged_halt,
            goal_text=goal.original_input or "",
        )
        prior_lessons = list(metadata.get("failure_lessons") or [])
        prior_lessons.append(lesson)
        learned = list(
            dict.fromkeys(
                [
                    *list(metadata.get("learned_constraints") or []),
                    *lesson["learned_constraints"],
                ]
            )
        )[:16]
        nonce = f"session:{bumped.epoch}:{gap_kind}:{lesson['lesson_digest']}"
        metadata["delivery_gap_reasons"] = reasons
        metadata["delivery_gap_kind"] = gap_kind
        metadata["execution_stage"] = "GENERATING"
        metadata["awaiting_authorized_sources"] = False
        metadata["failure_lessons"] = prior_lessons[-8:]
        metadata["learned_constraints"] = learned
        metadata["replan_nonce"] = nonce
        metadata["project_agent_session_id"] = str(bumped.id)
        metadata["project_agent_session_epoch"] = bumped.epoch
        metadata["project_agent_session_workspace_uri"] = bumped.workspace_uri
        metadata["session_resume_attempts"] = (
            int(metadata.get("session_resume_attempts") or 0) + 1
        )
        # Do not advance ATTRIBUTE_3 ladder counters on session resume.
        metadata["capability_resolution"] = {
            **dict(metadata.get("capability_resolution") or {}),
            "delivery_method": "SESSION_RESUME",
            "escalation_step": "SESSION_RESUME",
            "delivery_gap_kind": gap_kind,
            "generation_guidance": [
                f"Resume ProjectAgentSession {bumped.id} epoch={bumped.epoch} "
                f"gap_kind={gap_kind}.",
                "Continue in the same workspace; fix verification gaps with AgentRunner.",
                *[f"Constraint: {c}" for c in learned[:6]],
            ],
            "replan_nonce": nonce,
            "failure_lesson_digest": lesson["lesson_digest"],
            "project_agent_session_id": str(bumped.id),
            "project_agent_session_workspace_uri": bumped.workspace_uri,
        }
        metadata.update(
            merge_live_action_into_metadata(
                metadata,
                "正在同一 Agent Session 中根据验证反馈继续修复…",
                stage="GENERATING",
                event_type="PROJECT_AGENT_SESSION_RESUME",
            )
        )
        goal.metadata_json = metadata
        flag_modified(goal, "metadata_json")
        goal.version = int(goal.version or 0) + 1

        resume_key = make_idempotency_key(
            "generation-session-resume",
            goal.id,
            f"{requirement_revision_id}:{bumped.id}:{bumped.epoch}:{lesson['lesson_digest']}",
        )
        session.add(
            make_outbox_event(
                EventEnvelope(
                    event_type=GENERATION_RUN_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal.id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal.id),
                        "app_project_id": str(project_id),
                        "requirement_revision_id": str(requirement_revision_id),
                        "capability_resolution_plan_id": str(
                            capability_resolution_plan_id
                        ),
                        "actor": actor,
                        "idempotency_key": resume_key,
                        "delivery_policy": _DELIVERY_POLICY,
                        "delivery_gap_kind": gap_kind,
                        "escalation_step": "SESSION_RESUME",
                        "gap_reasons": reasons,
                        "replan_nonce": nonce,
                        "failure_lesson_digest": lesson["lesson_digest"],
                        "project_agent_session_id": str(bumped.id),
                        "project_agent_session_epoch": bumped.epoch,
                        "project_agent_session_workspace_uri": bumped.workspace_uri,
                    },
                    idempotency_key=resume_key,
                    correlation_id=goal.correlation_id,
                )
            )
        )
        message = (
            f"交付未达成（{', '.join(reasons[:3]) or 'review failed'}；"
            f"gap_kind={gap_kind}）。"
            f"已回到同一 ProjectAgentSession 续跑 AgentRunner"
            f"（session={bumped.id} epoch={bumped.epoch}），"
            "不升 ATTRIBUTE_3 能力阶梯。"
        )
        await self._append(
            session,
            project_id,
            role="ASSISTANT",
            message_type="PROJECT_AGENT_SESSION_RESUMED",
            content=message,
            metadata={
                "goal_id": str(goal.id),
                "method": "SESSION_RESUME",
                "gap_reasons": reasons,
                "gap_kind": gap_kind,
                "project_agent_session_id": str(bumped.id),
                "project_agent_session_epoch": bumped.epoch,
                "replan_nonce": nonce,
                "failure_lesson_digest": lesson["lesson_digest"],
                "total_attempts": total_attempts,
            },
        )
        return DeliveryGapRecoveryResult(
            True,
            "SESSION_RESUME",
            message,
            total_attempts,
            gap_kind,
        )

    async def _commit_recovery_escalation(
        self,
        session: AsyncSession,
        *,
        goal: GoalModel,
        project_id: uuid.UUID,
        requirement_revision_id: uuid.UUID,
        capability_resolution_plan_id: uuid.UUID,
        actor: str,
        gap_kind: str,
        reasons: list[str],
        guidance: tuple[str, ...],
        merged_halt: dict[str, Any],
        plan_attempt: int,
        plan_step: EscalationStep,
        method: str,
        primary_name: str,
        primary_id: uuid.UUID,
        extra_guidance: list[str],
        surface_id: uuid.UUID,
        http_id: uuid.UUID,
        review_id: uuid.UUID,
        effective_max: int,
    ) -> DeliveryGapRecoveryResult:
        """Write reorg + outbox + conversation after a ladder step is resolved."""
        metadata = dict(goal.metadata_json or {})
        all_guidance = list(dict.fromkeys([*extra_guidance, *guidance]))

        reorg = await self._orgs.reorganize_for_gap(
            session,
            goal_id=goal.id,
            gap_kind=gap_kind,
            method=method,
            capability_names=[
                primary_name,
                PRODUCT_SURFACE_NAME,
                HTTP_SOURCE_NAME,
                DELIVERY_REVIEW_NAME,
            ],
            attempt=plan_attempt,
            actor=actor,
        )

        lesson = build_failure_lesson(
            gap_reasons=reasons,
            gap_kind=gap_kind,
            method=method,
            attempt=plan_attempt,
            halt_context=merged_halt,
            goal_text=goal.original_input or "",
        )
        prior_lessons = list(metadata.get("failure_lessons") or [])
        prior_lessons.append(lesson)
        learned = list(
            dict.fromkeys(
                [
                    *list(metadata.get("learned_constraints") or []),
                    *lesson["learned_constraints"],
                ]
            )
        )[:_MAX_LEARNED_CONSTRAINTS]
        lesson_guidance = [
            f"Replan from failure lesson {lesson['lesson_digest']}: "
            f"attempt={plan_attempt} method={method} gap_kind={gap_kind}.",
            *[f"Constraint: {c}" for c in learned[:6]],
        ]
        all_guidance = list(dict.fromkeys([*lesson_guidance, *all_guidance]))

        metadata["delivery_gap_recovery_attempts"] = plan_attempt
        metadata["delivery_policy"] = _DELIVERY_POLICY
        metadata["delivery_gap_reasons"] = reasons
        metadata["delivery_gap_kind"] = gap_kind
        metadata["execution_stage"] = "GENERATING"
        metadata["awaiting_authorized_sources"] = False
        metadata["organization_id"] = str(reorg.receipt.organization_id)
        metadata["organization_strategy"] = reorg.receipt.strategy
        metadata["failure_lessons"] = prior_lessons[-_MAX_FAILURE_LESSONS:]
        metadata["learned_constraints"] = learned
        metadata["replan_nonce"] = (
            f"{plan_attempt}:{gap_kind}:{method}:{lesson['lesson_digest']}"
        )
        metadata["capability_resolution"] = {
            **dict(metadata.get("capability_resolution") or {}),
            "delivery_method": method,
            "escalation_step": plan_step.value,
            "delivery_gap_kind": gap_kind,
            "primary_capability": primary_name,
            "primary_capability_id": str(primary_id),
            "product_surface_capability_id": str(surface_id),
            "delivery_review_capability_id": str(review_id),
            "allowlisted_http_capability_id": str(http_id),
            "recovery_work_id": str(reorg.recovery_work_id),
            "organization_id": str(reorg.receipt.organization_id),
            "generation_guidance": all_guidance,
            "replan_nonce": metadata["replan_nonce"],
            "failure_lesson_digest": lesson["lesson_digest"],
        }
        goal.metadata_json = metadata

        resume_key = make_idempotency_key(
            "generation-delivery-recovery",
            goal.id,
            f"{requirement_revision_id}:{plan_attempt}:{gap_kind}:{method}:"
            f"{lesson['lesson_digest']}",
        )
        session.add(
            make_outbox_event(
                EventEnvelope(
                    event_type=GENERATION_RUN_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal.id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal.id),
                        "app_project_id": str(project_id),
                        "requirement_revision_id": str(requirement_revision_id),
                        "capability_resolution_plan_id": str(
                            capability_resolution_plan_id
                        ),
                        "actor": actor,
                        "idempotency_key": resume_key,
                        "delivery_policy": _DELIVERY_POLICY,
                        "delivery_gap_recovery_attempt": plan_attempt,
                        "delivery_gap_kind": gap_kind,
                        "escalation_step": method,
                        "gap_reasons": reasons,
                        "replan_nonce": metadata["replan_nonce"],
                        "failure_lesson_digest": lesson["lesson_digest"],
                    },
                    idempotency_key=resume_key,
                    correlation_id=goal.correlation_id,
                )
            )
        )
        guidance_preview = " ".join(all_guidance[:2])
        message = (
            f"交付未达成 Goal（{', '.join(reasons[:3]) or 'review failed'}；"
            f"gap_kind={gap_kind}）。"
            f"已吸收失败经验并重规划（lesson={lesson['lesson_digest']}）。"
            f"ATTRIBUTE_3 {method} → {primary_name}；ATTRIBUTE_4 重组组织 "
            f"{reorg.receipt.strategy}（attempt {plan_attempt}/{effective_max}）。"
            f"不发布不可靠表面。{guidance_preview}"
        )
        await self._append(
            session,
            project_id,
            role="ASSISTANT",
            message_type="DELIVERY_GAP_CAPABILITY_ESCALATED",
            content=message,
            metadata={
                "goal_id": str(goal.id),
                "attempt": plan_attempt,
                "method": method,
                "gap_reasons": reasons,
                "gap_kind": gap_kind,
                "capability_id": str(primary_id),
                "capability_name": primary_name,
                "organization_id": str(reorg.receipt.organization_id),
                "recovery_work_id": str(reorg.recovery_work_id),
                "replan_nonce": metadata["replan_nonce"],
                "failure_lesson_digest": lesson["lesson_digest"],
            },
        )
        logger.info(
            "delivery gap escalated with replan lesson",
            extra={
                "goal_id": str(goal.id),
                "attempt": plan_attempt,
                "method": method,
                "gap_kind": gap_kind,
                "org": str(reorg.receipt.organization_id),
                "replan_nonce": metadata["replan_nonce"],
            },
        )
        return DeliveryGapRecoveryResult(
            True,
            method,
            message,
            plan_attempt,
            gap_kind,
            recovery_work_id=reorg.recovery_work_id,
            organization_id=reorg.receipt.organization_id,
        )

    @staticmethod
    async def _soft_pause_delivery(
        session: AsyncSession,
        *,
        goal: GoalModel,
        project_id: uuid.UUID,
        metadata: dict[str, Any],
        gap_kind: str,
        reasons: list[str],
        attempts: int,
        message: str,
        summary: str,
        extra_termination: dict[str, Any] | None = None,
    ) -> DeliveryGapRecoveryResult:
        """Soft-pause after auto-continue budget is spent — no permission TaskCard.

        Product rule: humans only for permission/danger. Delivery gaps stay on
        conversation notes; chat can supply new direction without「总是允许」.
        """
        metadata["execution_stage"] = "DELIVERY_SOFT_PAUSE"
        metadata["awaiting_authorized_sources"] = False
        metadata["awaiting_human_intervention"] = False
        metadata.pop("pending_delivery_gap_human", None)
        metadata["delivery_gap_kind"] = gap_kind
        metadata["delivery_state"] = DeliveryState.DELIVERED_FOR_REVIEW.value
        # Sticky marker so in-flight workers / outbox cannot silently resume burn.
        metadata["ops_soft_pause"] = {
            "at": datetime.now(UTC).isoformat(),
            "reason": "goal_attainment_soft_pause",
            "gap_kind": gap_kind,
            "attempts": attempts,
        }
        draft_uri = str(
            (extra_termination or {}).get("draft_uri")
            or metadata.get("last_good_draft_uri")
            or ""
        ).strip()
        if draft_uri:
            metadata["last_good_draft_uri"] = draft_uri
        # A0: hard-cap soft-pause is STOP (not silent retry fuel).
        from regent.application.agent_loop_exit import (
            apply_exit_to_metadata,
            build_exit,
        )

        stop_reason = "budget" if gap_kind == "BUDGET_EXHAUSTED" else "hard_cap_soft_pause"
        if (extra_termination or {}).get("total_attempts_cap"):
            stop_reason = "total_attempts_hard_cap"
        metadata = apply_exit_to_metadata(
            metadata,
            build_exit(
                exit_kind="STOP",
                stop_reason=stop_reason,
                session_id=metadata.get("project_agent_session_id"),
                epoch=metadata.get("project_agent_session_epoch"),
                draft_uri=draft_uri or None,
            ),
        )
        preview_endpoint = str(metadata.get("last_preview_endpoint") or "").strip()
        metadata["termination"] = {
            "reason": "goal_attainment_soft_pause",
            # 3.0: resource ceilings (ATTRIBUTE_6) + stage goal may pause/hand over
            # while learning is retained (ATTRIBUTE_9). 1.0 ATTRIBUTE_7 was
            # "explicit termination" and has no 3.0 counterpart.
            "definition": "REGENT-DEFINITION-3.0 ATTRIBUTE_6/9",
            "gap_reasons": reasons,
            "gap_kind": gap_kind,
            "attempts_tried": attempts,
            "gac": "GAC-D1",
            "handoff": "SOFT_PAUSE",
            "draft_uri": draft_uri or None,
            "preview_endpoint": preview_endpoint or None,
            **(extra_termination or {}),
        }
        # Promote sandbox leftovers into a Console-safe DiagnosticDelivery.
        from regent.application.diagnostic_delivery import (
            build_diagnostic_delivery,
            public_diagnostic_delivery,
        )
        from regent.config import get_settings

        diagnostic = build_diagnostic_delivery(
            goal_id=goal.id,
            terminal_reason=(
                "BUDGET_EXHAUSTED"
                if gap_kind == "BUDGET_EXHAUSTED"
                or any("BUDGET_EXHAUSTED" in str(r).upper() for r in reasons)
                else "DELIVERY_SOFT_PAUSE"
            ),
            gap_kind=gap_kind,
            reasons=reasons,
            draft_uri=draft_uri or None,
            preview_endpoint=preview_endpoint or None,
            workspace_root=get_settings().workspace_root,
            summary=summary,
            attempts=attempts,
        )
        public = public_diagnostic_delivery(diagnostic)
        metadata["diagnostic_delivery"] = public
        metadata["delivery_state"] = DeliveryState.DELIVERED_FOR_REVIEW.value
        snap_id = (public.get("resume") or {}).get("base_snapshot_id")
        if snap_id:
            metadata["last_recoverable_workspace"] = {
                "snapshot_id": snap_id,
                "reason": gap_kind,
                "at": datetime.now(UTC).isoformat(),
            }
            metadata["last_recoverable_workspace_uri"] = diagnostic.get("_snapshot_uri")
        # Stop all "still running" UI: do not leave live_action behind.
        metadata.pop("live_action", None)
        metadata["execution_stage"] = "DELIVERY_SOFT_PAUSE"
        goal.metadata_json = metadata
        flag_modified(goal, "metadata_json")

        # Fail any in-flight GENERATING runs so console cannot show calling_model.
        from regent.infrastructure.models import (
            GenerationPlanModel,
            GenerationRunModel,
            RequirementRevisionModel,
        )

        run_ids = list(
            await session.scalars(
                select(GenerationRunModel.id)
                .join(
                    GenerationPlanModel,
                    GenerationRunModel.plan_id == GenerationPlanModel.id,
                )
                .join(
                    RequirementRevisionModel,
                    GenerationPlanModel.requirement_revision_id
                    == RequirementRevisionModel.id,
                )
                .where(
                    RequirementRevisionModel.goal_id == goal.id,
                    GenerationRunModel.status == "GENERATING",
                )
            )
        )
        for rid in run_ids:
            run = await session.get(GenerationRunModel, rid)
            if run is None:
                continue
            run.status = "FAILED"
            run.failure_code = (
                "BUDGET_EXHAUSTED"
                if public.get("terminal_reason") == "BUDGET_EXHAUSTED"
                else "OPS_SOFT_PAUSE_DIAGNOSTIC"
            )

        await DeliveryGapRecoveryService._append(
            session,
            project_id,
            role="ASSISTANT",
            message_type="DIAGNOSTIC_DELIVERY_READY",
            content=public.get("summary") or summary,
            metadata={
                "goal_id": str(goal.id),
                "app_project_id": str(project_id),
                "attempts": attempts,
                "gap_reasons": reasons,
                "gap_kind": gap_kind,
                "handoff": "SOFT_PAUSE",
                "detail": message[:800],
                "draft_uri": None,  # never file:// to console
                "preview_endpoint": preview_endpoint or None,
                "diagnostic_delivery": public,
                "message_type": "DIAGNOSTIC_DELIVERY_READY",
            },
        )
        return DeliveryGapRecoveryResult(
            False,
            "SOFT_PAUSE",
            message,
            attempts,
            gap_kind,
            terminal_exhaust=True,
        )

    async def resume_after_human(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        actor: str,
        human_message: str | None = None,
        option_id: str | None = None,
    ) -> DeliveryGapRecoveryResult:
        """After human authorizes continue: reset ladder counter and re-enter recover/replan.

        Chat「批准」must not only flip WAITING_HUMAN→ACTIVE (fake resume). Without this,
        delivery_gap_recovery_attempts stays exhausted and nothing regenerates.
        """
        gap_reasons: list[str] = []
        legacy_req_id: uuid.UUID | None = None
        legacy_plan_id: uuid.UUID | None = None
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return DeliveryGapRecoveryResult(
                    False, "BLOCK", "goal not found", 0, terminal_exhaust=False
                )
            metadata = dict(goal.metadata_json or {})
            # Session chassis is created at GoalExecutionService.start and stamped
            # onto metadata. recover() prefers SESSION_RESUME when that id is present.
            termination = dict(metadata.get("termination") or {})
            pending = dict(metadata.get("pending_delivery_gap_human") or {})
            raw_reasons = (
                pending.get("gap_reasons")
                or termination.get("gap_reasons")
                or metadata.get("delivery_gap_reasons")
                or []
            )
            if isinstance(raw_reasons, list):
                gap_reasons = [str(r) for r in raw_reasons if str(r).strip()][:12]
            if human_message and human_message.strip():
                gap_reasons = [
                    f"human-authorized-continue: {human_message.strip()[:200]}",
                    *gap_reasons,
                ][:12]
            if not gap_reasons:
                gap_reasons = [
                    "human-authorized-continue: ladder was exhausted; replan required"
                ]

            metadata["delivery_gap_recovery_attempts"] = 0
            metadata["delivery_gap_kind_streak"] = 0
            metadata["delivery_gap_auto_continue_cycles"] = 0
            metadata["delivery_gap_total_attempts"] = 0
            metadata["awaiting_human_intervention"] = False
            metadata.pop("termination", None)
            metadata.pop("pending_delivery_gap_human", None)
            metadata.pop("ops_soft_pause", None)
            # A0: mark ASK answered so exit gate allows authorized Session resume.
            from regent.application.agent_loop_exit import mark_ask_answered

            pending_ask_before = dict(metadata.get("pending_agent_loop_ask") or {})
            effective_option = (option_id or "").strip()
            if not effective_option and human_message:
                msg_l = human_message.strip().lower()
                for cand in (
                    "allow_always_session",
                    "allow_once",
                    "approve_plan",
                    "deny",
                    "stop",
                    "continue_fix",
                    "revise_plan",
                    "self_repair",
                    "replan_global",
                ):
                    if cand in msg_l or cand.replace("_", " ") in msg_l:
                        effective_option = cand
                        break
            if not effective_option:
                effective_option = str(pending_ask_before.get("suggested") or "continue_fix")

            # Progress ROI gate: rewrite empty continue_fix / stop burn when stagnant.
            from regent.application.progress_roi import (
                authorize_resume_by_roi,
                stamp_cycle_start,
                build_progress_snapshot,
                compute_workspace_hash,
                _workspace_root_from_metadata,
            )

            settings = get_settings()
            roi_enforced = bool(getattr(settings, "progress_roi_enforced", True))
            auth = authorize_resume_by_roi(
                metadata,
                option_id=effective_option,
                human_message=human_message,
                enforced=roi_enforced,
                stagnant_stop=int(
                    getattr(settings, "progress_roi_stagnant_stop", 3) or 3
                ),
            )
            effective_option = str(auth.get("option_id") or effective_option)
            if auth.get("force_stop") or not auth.get("allowed"):
                from regent.application.agent_loop_exit import (
                    apply_exit_to_metadata,
                    build_ask_envelope,
                    build_exit,
                    conversation_copy_for_exit,
                )
                from regent.application.progress_roi import (
                    enrich_ask_with_roi,
                    META_PROGRESS_ROI,
                    roi_ask_options,
                )

                roi_state = dict(metadata.get(META_PROGRESS_ROI) or {})
                msg = str(
                    auth.get("message")
                    or roi_state.get("summary")
                    or "Progress ROI：无进步，已停烧。"
                )
                ask = build_ask_envelope(
                    question=msg[:800],
                    why_blocked="progress_roi_stop",
                    options=roi_ask_options("stop"),
                    suggested="stop",
                    ask_type="progress_roi",
                    gap_kind=str(metadata.get("delivery_gap_kind") or "product_surface"),
                    gap_reasons=list(metadata.get("delivery_gap_reasons") or [])[:12],
                )
                ask = enrich_ask_with_roi(ask, roi_state, enforced=roi_enforced) or ask
                exit_payload = build_exit(
                    exit_kind="STOP",
                    stop_reason="doom_loop:roi_no_progress",
                    ask_envelope=ask,
                )
                metadata = apply_exit_to_metadata(metadata, exit_payload)
                metadata["execution_stage"] = "DELIVERY_SOFT_PAUSE"
                metadata["awaiting_human_intervention"] = True
                metadata.pop("authorized_session_resume", None)
                msg_type, content = conversation_copy_for_exit(exit_payload)
                goal.metadata_json = merge_live_action_into_metadata(
                    metadata,
                    content.split("\n")[0][:120],
                    stage="DELIVERY_SOFT_PAUSE",
                    event_type=msg_type,
                )
                flag_modified(goal, "metadata_json")
                await self._append(
                    session,
                    project_id,
                    role="ASSISTANT",
                    message_type="AGENT_LOOP_STOP",
                    content=content,
                    metadata={
                        "goal_id": str(goal_id),
                        "progress_roi": True,
                        "auth_reason": auth.get("reason"),
                    },
                )
                return DeliveryGapRecoveryResult(
                    False,
                    "ROI_STOP",
                    str(auth.get("reason") or "roi_stop_no_progress"),
                    0,
                    str(metadata.get("delivery_gap_kind") or "product_surface"),
                    terminal_exhaust=True,
                )

            if auth.get("reset_streak"):
                roi = dict(metadata.get("progress_roi") or {})
                roi["stagnant_streak"] = 0
                roi["next_action"] = "continue_fix"
                roi["updated_at"] = datetime.now(UTC).isoformat()
                metadata["progress_roi"] = roi

            inject = [str(c) for c in list(auth.get("inject_constraints") or []) if str(c).strip()]
            if inject:
                learned = list(metadata.get("learned_constraints") or [])
                learned = list(dict.fromkeys([*inject, *learned]))[:16]
                metadata["learned_constraints"] = learned
                gap_reasons = [
                    f"progress-roi:{effective_option}",
                    *inject[:4],
                    *gap_reasons,
                ][:12]

            if auth.get("work_plan_replan") or effective_option == "replan_global":
                metadata["work_plan_replan_requested"] = True
                metadata["work_plan_approved"] = False
                metadata.pop("skip_plan_approve", None)

            if effective_option == "self_repair":
                # Keep same session; force repair brief into human-authorized reasons.
                gap_reasons = [
                    "progress-roi:self_repair — apply ROI repair_constraints this cycle",
                    *gap_reasons,
                ][:12]

            metadata = mark_ask_answered(
                metadata,
                answer=(human_message or "human approved continue")[:800],
                option_id=effective_option,
            )
            # Work Plan: human approve (or any authorized continue after plan_approve ASK).
            pending_ask = dict(metadata.get("pending_agent_loop_ask") or {})
            ask_type = str(pending_ask.get("ask_type") or pending_ask_before.get("ask_type") or "")
            if (
                ask_type == "plan_approve"
                or str(metadata.get("delivery_gap_kind") or "") == "PLAN_APPROVE"
            ):
                if effective_option != "stop":
                    metadata["work_plan_approved"] = True
                    metadata["work_plan_seen"] = True
                    # After plan approve, allow checklist writes in ask mode without
                    # re-prompting every write_file (run_command still gated).
                    from regent.application.agent_control import grant_session_always

                    metadata = grant_session_always(metadata, "write_file")
                    metadata = grant_session_always(metadata, "edit_file")
                    # Ship-first: approved plan typically includes install/test/smoke shell steps.
                    metadata = grant_session_always(metadata, "run_command")
            # H0 Permission grants (session-scoped always / once via metadata for next lease).
            if ask_type == "permission" or str(metadata.get("delivery_gap_kind") or "") == "TOOL_PERMISSION":
                from regent.application.agent_control import grant_session_always

                tool_hint = ""
                for r in gap_reasons:
                    if "TOOL_PERMISSION_REQUIRED:" in str(r):
                        tool_hint = str(r).split(":", 1)[-1].strip()
                if effective_option == "allow_always_session" and tool_hint:
                    metadata = grant_session_always(metadata, tool_hint)
                elif effective_option == "allow_once" and tool_hint:
                    once = list(metadata.get("permission_allow_once_tools") or [])
                    if tool_hint not in once:
                        once.append(tool_hint)
                    metadata["permission_allow_once_tools"] = once[:32]
                elif effective_option == "deny":
                    metadata["permission_denied_at"] = datetime.now(UTC).isoformat()
            # Progress-loop / PROGRESS_LOOP: human answer resets streak so resume
            # does not immediately re-ASK on the same counter.
            from regent.application.agent_loop_exit import (
                META_PROGRESS_LOOP,
                advance_progress_item,
            )

            gap_kind_now = str(metadata.get("delivery_gap_kind") or "")
            if (
                ask_type == "progress_loop"
                or gap_kind_now in {"PROGRESS_LOOP", "ASK_USER"}
                or META_PROGRESS_LOOP in metadata
            ):
                blocked = str(
                    pending_ask_before.get("blocked_item_key")
                    or (pending_ask.get("blocked_item_key") if pending_ask else "")
                    or ""
                ).strip()
                if blocked:
                    metadata = advance_progress_item(metadata, item_key=blocked)
                else:
                    metadata.pop(META_PROGRESS_LOOP, None)
                if effective_option == "skip_item":
                    metadata["work_plan_approved"] = False
                    metadata["work_plan_replan_requested"] = True
                    metadata.pop("skip_plan_approve", None)
            metadata.pop("agent_abort_requested", None)
            from regent.application.agent_control import clear_abort

            clear_abort(str(goal_id))
            # Start a new ROI spend cycle baseline before burning the next lease.
            try:
                ws_root = _workspace_root_from_metadata(metadata)
                ws_hash = compute_workspace_hash(ws_root)
                cycle_snap = build_progress_snapshot(
                    metadata,
                    workspace_hash=ws_hash,
                    ledger={},
                    gap_reasons=gap_reasons,
                    gap_kind=str(metadata.get("delivery_gap_kind") or ""),
                )
                metadata = stamp_cycle_start(metadata, cycle_snap)
            except Exception:
                logger.warning(
                    "progress_roi stamp_cycle_start failed",
                    extra={"goal_id": str(goal_id)},
                    exc_info=True,
                )
            metadata["execution_stage"] = "GENERATING"
            metadata["authorized_session_resume"] = True
            resume_label = {
                "self_repair": "已确认，按 Progress ROI 定向自修复续跑",
                "replan_global": "已确认，按 Progress ROI 全局重规划续跑",
            }.get(effective_option, "已确认，同一 Agent Session 继续修复")
            metadata["human_resume_nonce"] = (
                f"human:{datetime.now(UTC).isoformat()}:{uuid.uuid4().hex[:8]}"
            )
            goal.metadata_json = merge_live_action_into_metadata(
                metadata,
                resume_label,
                stage="GENERATING",
                event_type="ATTAINMENT_RECOVERY_STARTED",
            )
            flag_modified(goal, "metadata_json")

            req_id, plan_id = await self._resolve_generation_ids(session, goal_id)
            if req_id is None or plan_id is None:
                # Early-pipeline goals (discovery not finished) must not keep minting
                # DELIVERY_GAP_INTERVENE cards: approve → missing lineage → halt →
                # new intervene →「总是允许」死循环.
                allow_actions = [
                    a
                    for a in list(metadata.get("decision_allow_actions") or [])
                    if str(a) != "delivery_gap_intervene"
                ]
                if allow_actions:
                    metadata["decision_allow_actions"] = allow_actions
                else:
                    metadata.pop("decision_allow_actions", None)
                metadata["awaiting_human_intervention"] = False
                metadata["execution_stage"] = "DISCOVERING"
                metadata["lineage_missing_resume_at"] = datetime.now(UTC).isoformat()
                goal.metadata_json = merge_live_action_into_metadata(
                    metadata,
                    "已批准，但交付谱系未就绪；正在重新发起发现/规划，不再弹出同类「总是允许」卡",
                    stage="DISCOVERING",
                    event_type="LINEAGE_MISSING_RESTART_DISCOVERY",
                )
                flag_modified(goal, "metadata_json")
                await self._append(
                    session,
                    project_id,
                    role="ASSISTANT",
                    message_type="ATTAINMENT_RECOVERY_STARTED",
                    content=(
                        "已批准，但缺少生成谱系（requirement/plan）。"
                        "已清除「总是允许·交付缺口」以免空转；将重新发起发现，而不是再弹同一张批准卡。"
                    ),
                    metadata={
                        "goal_id": str(goal_id),
                        "human_resume": True,
                        "restart_discovery": True,
                    },
                )
                return DeliveryGapRecoveryResult(
                    False,
                    "RESTART_DISCOVERY",
                    "missing generation lineage after human approve",
                    0,
                    str(metadata.get("delivery_gap_kind") or "product_surface"),
                    terminal_exhaust=False,
                )

            # A0 authorized path: resume same Session when chassis id is present.
            # Ladder unit tests omit session_id — fall through to recover() unchanged.
            reasons = gap_reasons
            gap_kind = classify_delivery_gap_kind(reasons)
            merged_halt = {
                "stage": "HUMAN_AUTHORIZED_RESUME",
                "message": (human_message or "human approved continue")[:400],
                "last_error": "human-authorized-replan",
                "gac": "GAC-D1",
            }
            if str(metadata.get("project_agent_session_id") or "").strip():
                from regent.application.project_agent_session import (
                    SESSION_STATUS_ACTIVE,
                    SESSION_STATUS_PAUSED,
                    ProjectAgentSessionService,
                )
                from regent.infrastructure.models import ProjectAgentSessionModel

                paused = None
                active_check = None
                try:
                    paused_row = await session.scalar(
                        select(ProjectAgentSessionModel)
                        .where(
                            ProjectAgentSessionModel.app_project_id == project_id,
                            ProjectAgentSessionModel.status == SESSION_STATUS_PAUSED,
                        )
                        .order_by(ProjectAgentSessionModel.updated_at.desc())
                        .limit(1)
                    )
                    if isinstance(paused_row, ProjectAgentSessionModel):
                        paused = paused_row
                        paused.status = SESSION_STATUS_ACTIVE
                        paused.version = int(paused.version or 0) + 1
                    active_check = await ProjectAgentSessionService(
                        self._sessions
                    ).get_active_in(session, project_id)
                except (StopAsyncIteration, StopIteration):
                    paused = None
                    active_check = None
                if active_check is not None or paused is not None:
                    resumed = await self._resume_same_agent_session(
                        session,
                        goal=goal,
                        project_id=project_id,
                        requirement_revision_id=req_id,
                        capability_resolution_plan_id=plan_id,
                        actor=actor,
                        reasons=reasons,
                        gap_kind=gap_kind,
                        metadata=dict(goal.metadata_json or {}),
                        merged_halt=merged_halt,
                        total_attempts=1,
                    )
                    if resumed is not None:
                        return resumed
                    return DeliveryGapRecoveryResult(
                        False,
                        "SESSION_RESUME_FAILED",
                        "authorized resume could not schedule Session lease",
                        0,
                        gap_kind,
                        terminal_exhaust=False,
                    )
            legacy_req_id, legacy_plan_id = req_id, plan_id

        return await self.recover(
            goal_id=goal_id,
            project_id=project_id,
            requirement_revision_id=legacy_req_id,
            capability_resolution_plan_id=legacy_plan_id,
            actor=actor,
            gap_reasons=gap_reasons,
            halt_context={
                "stage": "HUMAN_AUTHORIZED_RESUME",
                "message": (human_message or "human approved continue")[:400],
                "last_error": "human-authorized-replan",
                "gac": "GAC-D1",
            },
        )

    @staticmethod
    async def _resolve_generation_ids(
        session: AsyncSession, goal_id: uuid.UUID
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        goal = await session.get(GoalModel, goal_id)
        meta = dict((goal.metadata_json if goal else None) or {})
        raw_plan = meta.get("capability_resolution_plan_id")
        raw_req = meta.get("requirement_revision_id")
        if raw_plan and raw_req:
            return uuid.UUID(str(raw_req)), uuid.UUID(str(raw_plan))
        if raw_plan:
            plan = await session.get(
                CapabilityResolutionPlanModel, uuid.UUID(str(raw_plan))
            )
            if plan is not None:
                return plan.requirement_revision_id, plan.id
        gen = await session.scalar(
            select(GenerationPlanModel)
            .join(
                RequirementRevisionModel,
                RequirementRevisionModel.id == GenerationPlanModel.requirement_revision_id,
            )
            .join(
                ProductHypothesisModel,
                ProductHypothesisModel.id == RequirementRevisionModel.hypothesis_id,
            )
            .join(
                DiscoveryRoundModel,
                DiscoveryRoundModel.id == ProductHypothesisModel.round_id,
            )
            .where(DiscoveryRoundModel.goal_id == goal_id)
            .order_by(GenerationPlanModel.created_at.desc())
            .limit(1)
        )
        if gen is not None:
            return gen.requirement_revision_id, gen.capability_resolution_plan_id
        return None, None

    async def _admit_failure_memories(
        self,
        *,
        org_key: str,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        actor: str,
        lesson: dict[str, Any],
    ) -> None:
        """Persist episodic failure + reorganization memories; refresh REGENT.md gaps."""
        try:
            await self._memories.admit(
                AdmitMemory(
                    org_key=org_key or "default",
                    kind=MemoryKind.EPISODIC_RUN_FAILURE.value,
                    content={
                        "source": "delivery_gap_recovery",
                        "goal_text": lesson.get("goal_text") or "",
                        "verification_passed": False,
                        "verification_summary": (
                            f"delivery gap recovery attempt={lesson.get('attempt')} "
                            f"method={lesson.get('escalation_method')} "
                            f"gap_kind={lesson.get('gap_kind')}"
                        ),
                        "gaps": list(lesson.get("gap_reasons") or [])[:12],
                        "learned_constraints": list(
                            lesson.get("learned_constraints") or []
                        )[:16],
                        "lesson_digest": lesson.get("lesson_digest"),
                        "halt_stage": lesson.get("halt_stage") or "",
                        "last_error": lesson.get("last_error") or "",
                        "replan_required": True,
                    },
                    actor=actor,
                    goal_id=goal_id,
                    source_refs=[
                        {"type": "app_project", "id": str(project_id)},
                        {
                            "type": "replan_nonce",
                            "id": (
                                f"{lesson.get('attempt')}:"
                                f"{lesson.get('gap_kind')}:"
                                f"{lesson.get('lesson_digest')}"
                            ),
                        },
                    ],
                )
            )
            await self._memories.admit(
                AdmitMemory(
                    org_key=org_key or "default",
                    kind=MemoryKind.EPISODIC_REORGANIZATION.value,
                    content={
                        "source": "delivery_gap_recovery",
                        "gap_kind": lesson.get("gap_kind"),
                        "method": lesson.get("escalation_method"),
                        "attempt": lesson.get("attempt"),
                        "lesson_digest": lesson.get("lesson_digest"),
                        "learned_constraints": list(
                            lesson.get("learned_constraints") or []
                        )[:8],
                    },
                    actor=actor,
                    goal_id=goal_id,
                )
            )
        except Exception:
            logger.warning(
                "failed to admit delivery-gap failure memory",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )
        try:
            from regent.agent.project_memory import ProjectMemoryService
            from regent.config import get_settings

            settings = get_settings()
            memory = ProjectMemoryService(
                self._sessions,
                projects_root=Path(settings.workspace_root) / "project_memory",
            )
            existing = memory.load_regent_md(project_id)
            distilled = memory.distill_regent_md(
                existing=existing,
                goal_text=str(lesson.get("goal_text") or ""),
                stack_hints=[],
                structure=[],
                gaps=[
                    *list(lesson.get("gap_reasons") or [])[:8],
                    *list(lesson.get("learned_constraints") or [])[:8],
                ],
                verification_summary=(
                    f"recovery replan lesson={lesson.get('lesson_digest')} "
                    f"method={lesson.get('escalation_method')}"
                ),
            )
            memory.write_regent_md(project_id, distilled)
        except Exception:
            logger.warning(
                "failed to distill REGENT.md after delivery-gap recovery",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )

    async def prepare_gate_reorganization(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        actor: str,
        gate_status: str,
    ) -> DeliveryGapRecoveryResult:
        """GAC-D4: before EXHAUST on gate failure, escalate capability + org once more.

        When ``terminal_exhaust=True``, callers must route via
        ``_apply_delivery_verdict`` / ``WAIT_FOR_HUMAN`` (``HUMAN_TASK_REQUIRED``);
        this method signals exhaustion only — it does not invent a calm STOP.
        """
        surface_id = await ensure_product_surface_capability(self._sessions)
        review_id = await ensure_delivery_review_capability(self._sessions)
        http_id = await ensure_allowlisted_http_capability(self._sessions)
        gap_kind = "gate_failed"
        guidance = guidance_for_gap_kind(gap_kind)
        pending = False
        pending_attempts = 0

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return DeliveryGapRecoveryResult(
                    False, "BLOCK", "goal not found", 0, gap_kind
                )
            metadata = dict(goal.metadata_json or {})
            # I-E: while ProjectAgentSession is ACTIVE, do not expand ATTRIBUTE_3 /
            # org as the "brain" — Session + AgentRunner remains the controller.
            if bool(getattr(get_settings(), "agent_session_resume_enabled", True)) and metadata.get(
                "project_agent_session_id"
            ):
                from regent.application.project_agent_session import ProjectAgentSessionService

                active = await ProjectAgentSessionService(self._sessions).get_active_in(
                    session, project_id
                )
                if active is not None:
                    return DeliveryGapRecoveryResult(
                        False,
                        "SESSION_ACTIVE",
                        (
                            "ProjectAgentSession still ACTIVE; skip gate capability/org "
                            "reorganization (I-E). Prefer Session resume / soft-pause."
                        ),
                        int(metadata.get("gate_reorg_attempts") or 0),
                        gap_kind,
                    )
            attempts = int(metadata.get("gate_reorg_attempts") or 0)
            # CD-7.3: Gate budget scales with delivery_profile (same authority as recover).
            _persona = getattr(get_settings(), "delivery_profile", "balanced")
            _GATE_MAX = gate_reorg_max(_persona)
            if attempts >= _GATE_MAX:
                return DeliveryGapRecoveryResult(
                    False,
                    "STOP",
                    (
                        f"Gate 自动重组已用尽（{attempts}/{_GATE_MAX} 次）；"
                        "需要你介入后继续，不会标记为已完成。"
                    ),
                    attempts,
                    gap_kind,
                    terminal_exhaust=True,
                )
            step_name = gate_reorg_step_name(attempts)
            step = EscalationStep(step_name)

            # CD-7.2: defer ACQUIRE network until outside this transaction.
            if step is EscalationStep.ACQUIRE:
                pending = True
                pending_attempts = attempts
            else:
                pending = False
                candidates = await self._load_candidates(
                    session,
                    surface_id=surface_id,
                    review_id=review_id,
                    http_id=http_id,
                )
                method, primary_name, primary_id, extra_guidance = await self._apply_step(
                    session,
                    goal_id=goal_id,
                    step=step,
                    gap_kind=gap_kind,
                    guidance=guidance,
                    reasons=[f"gate:{gate_status}"],
                    candidates=candidates,
                    surface_id=surface_id,
                    http_id=http_id,
                    review_id=review_id,
                )
                return await self._commit_gate_reorg(
                    session,
                    goal=goal,
                    project_id=project_id,
                    actor=actor,
                    gate_status=gate_status,
                    gap_kind=gap_kind,
                    guidance=guidance,
                    attempts=attempts,
                    method=method,
                    primary_name=primary_name,
                    primary_id=primary_id,
                    extra_guidance=extra_guidance,
                )

        if pending:
            acquire_result = await CapabilityAcquireService(self._sessions).acquire(
                AcquireRequest(
                    capability_name=f"acquired-{gap_kind}-v1",
                    requirement_key=f"delivery.acquire.{gap_kind}",
                    goal_id=goal_id,
                    actor_id=actor,
                )
            )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id, with_for_update=True)
                if goal is None:
                    return DeliveryGapRecoveryResult(
                        False, "BLOCK", "goal not found", 0, gap_kind
                    )
                metadata = dict(goal.metadata_json or {})
                attempts_now = int(metadata.get("gate_reorg_attempts") or 0)
                if attempts_now != pending_attempts:
                    return DeliveryGapRecoveryResult(
                        False,
                        "BLOCK",
                        "concurrent gate reorg changed attempts; acquire discarded",
                        attempts_now,
                        gap_kind,
                    )
                candidates = await self._load_candidates(
                    session,
                    surface_id=surface_id,
                    review_id=review_id,
                    http_id=http_id,
                )
                method, primary_name, primary_id, extra_guidance = await self._apply_step(
                    session,
                    goal_id=goal_id,
                    step=EscalationStep.ACQUIRE,
                    gap_kind=gap_kind,
                    guidance=guidance,
                    reasons=[f"gate:{gate_status}"],
                    candidates=candidates,
                    surface_id=surface_id,
                    http_id=http_id,
                    review_id=review_id,
                    acquire_result=acquire_result,
                )
                return await self._commit_gate_reorg(
                    session,
                    goal=goal,
                    project_id=project_id,
                    actor=actor,
                    gate_status=gate_status,
                    gap_kind=gap_kind,
                    guidance=guidance,
                    attempts=pending_attempts,
                    method=method,
                    primary_name=primary_name,
                    primary_id=primary_id,
                    extra_guidance=extra_guidance,
                )
        raise RuntimeError("prepare_gate_reorganization: unreachable")

    async def _commit_gate_reorg(
        self,
        session: AsyncSession,
        *,
        goal: GoalModel,
        project_id: uuid.UUID,
        actor: str,
        gate_status: str,
        gap_kind: str,
        guidance: tuple[str, ...],
        attempts: int,
        method: str,
        primary_name: str,
        primary_id: uuid.UUID,
        extra_guidance: list[str],
    ) -> DeliveryGapRecoveryResult:
        metadata = dict(goal.metadata_json or {})
        next_attempt = attempts + 1
        reorg = await self._orgs.reorganize_for_gap(
            session,
            goal_id=goal.id,
            gap_kind=gap_kind,
            method=method,
            capability_names=[
                primary_name,
                PRODUCT_SURFACE_NAME,
                HTTP_SOURCE_NAME,
            ],
            attempt=next_attempt,
            actor=actor,
        )
        metadata["gate_reorg_attempts"] = next_attempt
        metadata["execution_stage"] = "REORGANIZING"
        metadata["organization_id"] = str(reorg.receipt.organization_id)
        metadata["organization_strategy"] = reorg.receipt.strategy
        metadata["capability_resolution"] = {
            **dict(metadata.get("capability_resolution") or {}),
            "gate_reorg_method": method,
            "primary_capability": primary_name,
            "primary_capability_id": str(primary_id),
            "generation_guidance": list(dict.fromkeys([*extra_guidance, *guidance])),
            "recovery_work_id": str(reorg.recovery_work_id),
        }
        goal.metadata_json = metadata
        message = (
            f"Gate {gate_status}: ATTRIBUTE_3/4 重组能力与组织 "
            f"({method} → {primary_name}, strategy={reorg.receipt.strategy})."
        )
        await self._append(
            session,
            project_id,
            role="ASSISTANT",
            message_type="GATE_CAPABILITY_REORGANIZED",
            content=message,
            metadata={
                "goal_id": str(goal.id),
                "attempt": next_attempt,
                "method": method,
                "gate_status": gate_status,
                "recovery_work_id": str(reorg.recovery_work_id),
                "organization_id": str(reorg.receipt.organization_id),
            },
        )
        return DeliveryGapRecoveryResult(
            True,
            method,
            message,
            next_attempt,
            gap_kind,
            recovery_work_id=reorg.recovery_work_id,
            organization_id=reorg.receipt.organization_id,
        )

    async def _apply_step(
        self,
        session: AsyncSession,
        *,
        goal_id: uuid.UUID,
        step: EscalationStep,
        gap_kind: str,
        guidance: tuple[str, ...],
        reasons: list[str],
        candidates: list[CapabilityCandidate],
        surface_id: uuid.UUID,
        http_id: uuid.UUID,
        review_id: uuid.UUID,
        acquire_result: AcquireResult | None = None,
    ) -> tuple[str, str, uuid.UUID, list[str]]:
        """Return (method, primary_name, primary_id, extra_guidance).

        ACQUIRE must be prefetched outside any open transaction (CD-7.2);
        pass ``acquire_result`` when ``step`` is ACQUIRE.
        """
        if step in {EscalationStep.REUSE, EscalationStep.CONFIGURE}:
            primary_name, requirement_key, primary_id = self._route_primary(
                gap_kind, surface_id=surface_id, http_id=http_id
            )
            plan = self._resolver.resolve(
                [
                    CapabilityGap(
                        requirement_key=requirement_key,
                        capability_name=primary_name,
                        build_allowed=False,
                        human_resolvable=True,
                    )
                ],
                candidates,
                [],
            )
            item = plan.items[0]
            if step is EscalationStep.CONFIGURE:
                extra = [
                    f"CONFIGURE {primary_name}: tighten bindings and generation guidance "
                    f"for gap_kind={gap_kind}; do not ship the prior rejected surface.",
                ]
                return "CONFIGURE", primary_name, primary_id or item.capability_id or surface_id, extra
            method = (
                item.method.value
                if item.method
                in {
                    ResolutionMethod.REUSE,
                    ResolutionMethod.CONFIGURE,
                    ResolutionMethod.COMPOSE,
                }
                else "REUSE"
            )
            return method, primary_name, primary_id or item.capability_id or surface_id, []

        if step is EscalationStep.COMPOSE:
            composed_name = composed_capability_name(gap_kind)
            parts = (PRODUCT_SURFACE_NAME, HTTP_SOURCE_NAME, DELIVERY_REVIEW_NAME)
            plan = self._resolver.resolve(
                [
                    CapabilityGap(
                        requirement_key=f"delivery.compose.{gap_kind}",
                        capability_name=composed_name,
                        composable_from=parts,
                        build_allowed=True,
                        human_resolvable=True,
                    )
                ],
                candidates,
                [],
            )
            item = plan.items[0]
            if item.method is ResolutionMethod.COMPOSE:
                # Register composed capability as GOAL_CERTIFIED so later REUSE works.
                composed_id = await build_attainment_capability(
                    session,
                    goal_id=goal_id,
                    capability_name=composed_name,
                    requirement_key=f"delivery.compose.{gap_kind}",
                    gap_kind=gap_kind,
                    guidance=(
                        f"COMPOSE {', '.join(parts)} for {gap_kind}.",
                        *guidance,
                    ),
                    acceptance_checks=reasons,
                    composable_from=parts,
                )
                extra = [
                    f"Compose {PRODUCT_SURFACE_NAME} + {HTTP_SOURCE_NAME} + "
                    f"{DELIVERY_REVIEW_NAME}; bind all three into one deliverable."
                ]
                return "COMPOSE", composed_name, composed_id, extra
            # Fall through to BUILD if compose unavailable.
            step = EscalationStep.BUILD

        # BUILD (and COMPOSE fallback)
        if step is not EscalationStep.ACQUIRE:
            built_name = built_capability_name(gap_kind)
            built_id = await build_attainment_capability(
                session,
                goal_id=goal_id,
                capability_name=built_name,
                requirement_key=f"delivery.build.{gap_kind}",
                gap_kind=gap_kind,
                guidance=(
                    f"BUILD {built_name}: close the {gap_kind} gap for Goal attainment.",
                    *guidance,
                ),
                acceptance_checks=reasons,
                composable_from=(PRODUCT_SURFACE_NAME, HTTP_SOURCE_NAME, DELIVERY_REVIEW_NAME),
            )
            # Keep candidate list consistent for resolution bookkeeping.
            self._resolver.resolve(
                [
                    CapabilityGap(
                        requirement_key=f"delivery.build.{gap_kind}",
                        capability_name=built_name,
                        build_allowed=True,
                        human_resolvable=False,
                    )
                ],
                [
                    *candidates,
                    CapabilityCandidate(built_id, built_name, "GOAL_CERTIFIED"),
                ],
                [],
            )
            extra = [
                f"Built capability {built_name} with acceptance checks; regenerate to satisfy them.",
            ]
            return "BUILD", built_name, built_id, extra

        # ACQUIRE: use prefetched network result (never httpx inside this txn).
        if acquire_result is None:
            raise RuntimeError(
                "ACQUIRE step requires acquire_result prefetched outside the "
                "write transaction (CD-7.2)"
            )
        acquire_name = f"acquired-{gap_kind}-v1"
        result = acquire_result
        if result.success and result.capability_id is not None:
            extra = [
                f"ACQUIRE {acquire_name} from {result.source_url}; "
                f"hash={result.source_hash[:12] if result.source_hash else 'n/a'}...",
            ]
            return "ACQUIRE", acquire_name, result.capability_id, extra

        # ACQUIRE failed — fall back to BUILD
        built_name = built_capability_name(gap_kind)
        built_id = await build_attainment_capability(
            session,
            goal_id=goal_id,
            capability_name=built_name,
            requirement_key=f"delivery.build.{gap_kind}",
            gap_kind=gap_kind,
            guidance=(
                f"BUILD {built_name} (ACQUIRE failed: {result.failure_reason}); "
                f"close the {gap_kind} gap for Goal attainment.",
                *guidance,
            ),
            acceptance_checks=reasons,
            composable_from=(PRODUCT_SURFACE_NAME, HTTP_SOURCE_NAME, DELIVERY_REVIEW_NAME),
        )
        extra = [
            f"ACQUIRE failed ({result.failure_reason}); fell back to BUILD {built_name}.",
        ]
        return "BUILD", built_name, built_id, extra

    @staticmethod
    async def _load_candidates(
        session: AsyncSession,
        *,
        surface_id: uuid.UUID,
        review_id: uuid.UUID,
        http_id: uuid.UUID,
    ) -> list[CapabilityCandidate]:
        out: list[CapabilityCandidate] = []
        for cap_id, name in (
            (surface_id, PRODUCT_SURFACE_NAME),
            (review_id, DELIVERY_REVIEW_NAME),
            (http_id, HTTP_SOURCE_NAME),
        ):
            row = await session.scalar(
                select(CapabilityModel).where(CapabilityModel.id == cap_id)
            )
            out.append(
                CapabilityCandidate(
                    id=cap_id,
                    name=name,
                    status=row.status if row else "VERIFIED",
                )
            )
        return out

    @staticmethod
    def _route_primary(
        gap_kind: str,
        *,
        surface_id: uuid.UUID,
        http_id: uuid.UUID,
    ) -> tuple[str, str, uuid.UUID]:
        if gap_kind == "evidence":
            return HTTP_SOURCE_NAME, "delivery.evidence_render", http_id
        if gap_kind == "presentation":
            return PRODUCT_SURFACE_NAME, "delivery.presentation", surface_id
        if gap_kind == "goal_intent":
            return PRODUCT_SURFACE_NAME, "delivery.goal_intent", surface_id
        return PRODUCT_SURFACE_NAME, "delivery.goal_attainment", surface_id

