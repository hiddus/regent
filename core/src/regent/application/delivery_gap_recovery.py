"""Recover delivery/goal-attainment gaps by organizing more capability — not shipping junk.

Per REGENT-DEFINITION-1.0:
- ATTRIBUTE_3: discover capability gaps; REUSE→CONFIGURE→COMPOSE→BUILD→request human last
- ATTRIBUTE_4: organization is a means; grow agents/capabilities when needed
- ATTRIBUTE_6: external outcome loop — do not treat generator self-score as Goal success
- ATTRIBUTE_7: explicit termination when Goal cannot be attained

GAC-D: escalate the ladder across attempts, reorganize the goal org, BUILD real packages.

Product principle: every retry must absorb prior failure experience and replan —
never blind same-input retries that re-hit the same wall.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from regent.application.confirmation_present import confirmation_for_human_task
from regent.application.decision_policy import action_preauthorized
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
    ConversationMessageModel,
    ConversationModel,
    DiscoveryRoundModel,
    GenerationPlanModel,
    GoalModel,
    GoalSpecModel,
    HumanTaskModel,
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

_PRESENTATION_MARKERS = (
    "stylesheet-present",
    "stylesheet-substance",
    "styled-surface",
    "stylesheet",
    "product-structure",
    "forbid-demo-shell",
    "semantic-main",
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
    constraints: list[str] = [
        f"Do not repeat the prior rejected surface for gap_kind={gap_kind}.",
        "Absorb prior failure lessons before emitting another deliverable.",
    ]
    joined = " ".join(r.lower() for r in gap_reasons)
    if "stylesheet" in joined or "styled-surface" in joined or gap_kind == "presentation":
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
    lesson = {
        "at": datetime.now(UTC).isoformat(),
        "attempt": attempt,
        "gap_kind": gap_kind,
        "escalation_method": method,
        "gap_reasons": list(gap_reasons)[:12],
        "learned_constraints": build_learned_constraints(gap_kind, gap_reasons),
        "halt_stage": str(halt.get("stage") or halt.get("execution_stage") or ""),
        "halt_message": str(halt.get("message") or "")[:400],
        "last_error": str(halt.get("last_error") or halt.get("error") or "")[:400],
        "goal_text": goal_text[:240],
        "replan_required": True,
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
            # 「总是允许」→ decision_allow_actions；仅在阶梯耗尽交人时跳过询问。
            gap_preauthorized = action_preauthorized(
                metadata, "delivery_gap_intervene"
            )

            # Same gap_kind hard cap: stop infinite escalate loops before ladder exhaust.
            from regent.application.delivery_success_policy import SAME_GAP_KIND_HARD_CAP

            prior_kind = str(metadata.get("delivery_gap_kind") or "")
            streak = int(metadata.get("delivery_gap_kind_streak") or 0)
            if prior_kind == gap_kind:
                streak += 1
            else:
                streak = 1
            metadata["delivery_gap_kind"] = gap_kind
            metadata["delivery_gap_kind_streak"] = streak
            if streak >= SAME_GAP_KIND_HARD_CAP and not gap_preauthorized:
                draft_note = (
                    f" 当前草稿：{draft_uri}" if draft_uri else ""
                )
                message = (
                    f"同一类交付缺口（{gap_kind}）已连续自动修复 {streak} 次仍未过关。"
                    "为避免空转，已暂停自动升级；请补充方向或批准后继续。"
                    f"{draft_note}"
                )
                return await self._handoff_to_human(
                    session,
                    goal=goal,
                    project_id=project_id,
                    actor=actor,
                    metadata=metadata,
                    gap_kind=gap_kind,
                    reasons=reasons,
                    attempts=attempts,
                    message=message,
                    task_summary=f"同类缺口已达硬顶（{gap_kind}×{streak}），需要你的方向",
                    task_rationale=(
                        "同一 gap_kind 自动修复已达硬顶；"
                        "补充方向或允许继续后将重置 streak 并重新规划。"
                    ),
                    extra_rules=["stage:DELIVERY_GAP_KIND_CAP", "gac:GAC-D1"],
                    extra_termination={
                        "same_gap_kind_cap": True,
                        "gap_kind_streak": streak,
                        "draft_uri": draft_uri or None,
                    },
                )

            # goal_intent / presentation / evidence 都是正常交付修复，不是危险动作：
            # 一律走能力阶梯自动重试。只有阶梯耗尽才需要人给新方向（见下方 handoff）。
            # （曾有 CD-1.3 对 goal_intent 早交人，导致「继续生成」也被当成高风险授权。）

            # AC5: persona scales the auto-recovery ladder. balanced -> unchanged.
            # CD-7.3: delivery_profile is the authority for recovery budgets.
            _persona = getattr(get_settings(), "delivery_profile", "balanced")
            pending_persona = str(_persona)
            _effective_max = int(
                round(MAX_ATTAINMENT_ESCALATION_ATTEMPTS * recovery_budget_multiplier(_persona))
            )
            plan = plan_escalation(attempts, max_attempts=_effective_max)

            if plan.exhausted or plan.step is EscalationStep.STOP:
                # Always-allow: open one fresh ladder cycle instead of re-prompting.
                if gap_preauthorized and attempts > 0:
                    attempts = 0
                    metadata["delivery_gap_recovery_attempts"] = 0
                    plan = plan_escalation(0, max_attempts=_effective_max)
                if plan.exhausted or plan.step is EscalationStep.STOP:
                    # Ladder spent: need human *direction*, not a "dangerous action" grant.
                    message = (
                        "交付仍未达成 Goal。已穷举 ATTRIBUTE_3 能力阶梯 "
                        f"（REUSE→CONFIGURE→COMPOSE→BUILD→ACQUIRE ×{ATTAINMENT_LADDER_CYCLES} 轮，"
                        f"共 {_effective_max} 次）。"
                        "拒绝发布不可靠表面；需要你补充方向或授权后继续，不会标记为已完成。"
                    )
                    return await self._handoff_to_human(
                        session,
                        goal=goal,
                        project_id=project_id,
                        actor=actor,
                        metadata=metadata,
                        gap_kind=gap_kind,
                        reasons=reasons,
                        attempts=attempts,
                        message=message,
                        task_summary="自动修复已用尽，需要你补充方向",
                        task_rationale=(
                            "自动修复轮次已用尽；"
                            "补充方向或允许继续后将重置计数并重新规划，否则保持等待。"
                        ),
                        extra_rules=["stage:DELIVERY_GAP_EXHAUSTED", "gac:GAC-D1"],
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
    async def _handoff_to_human(
        session: AsyncSession,
        *,
        goal: GoalModel,
        project_id: uuid.UUID,
        actor: str,
        metadata: dict[str, Any],
        gap_kind: str,
        reasons: list[str],
        attempts: int,
        message: str,
        task_summary: str,
        task_rationale: str,
        extra_rules: list[str],
        extra_termination: dict[str, Any] | None = None,
    ) -> DeliveryGapRecoveryResult:
        """Shared human-handoff path (ladder exhaustion + goal_intent short-circuit).

        CD-1.2/CD-1.3: current best output is never discarded (AC4) and
        ``delivery_state`` is written explicitly so DELIVERED_FOR_REVIEW is
        observable even before ``_apply_delivery_verdict`` re-confirms it.
        """
        metadata["execution_stage"] = "WAITING_HUMAN"
        metadata["awaiting_authorized_sources"] = False
        metadata["awaiting_human_intervention"] = True
        metadata["delivery_gap_kind"] = gap_kind
        metadata["delivery_state"] = DeliveryState.DELIVERED_FOR_REVIEW.value
        draft_uri = str(
            (extra_termination or {}).get("draft_uri")
            or metadata.get("last_good_draft_uri")
            or ""
        ).strip()
        if draft_uri:
            metadata["last_good_draft_uri"] = draft_uri
        preview_endpoint = str(metadata.get("last_preview_endpoint") or "").strip()
        metadata["termination"] = {
            "reason": "goal_attainment_needs_human",
            "definition": "REGENT-DEFINITION-1.0 ATTRIBUTE_7",
            "gap_reasons": reasons,
            "gap_kind": gap_kind,
            "attempts_tried": attempts,
            "gac": "GAC-D1",
            "handoff": "WAITING_HUMAN",
            "draft_uri": draft_uri or None,
            "preview_endpoint": preview_endpoint or None,
            **(extra_termination or {}),
        }
        task_id = uuid.uuid4()
        detail_parts = ["; ".join(reasons) if reasons else message[:500]]
        if preview_endpoint:
            detail_parts.append(f"可打开预览: {preview_endpoint}")
        if draft_uri:
            detail_parts.append(f"保留草稿: {draft_uri}")
        confirmation = confirmation_for_human_task(
            task_type="DELIVERY_GAP_INTERVENE",
            summary=task_summary,
            rationale=task_rationale,
            detail=" | ".join(detail_parts),
            prompt=message,
            extra_rules=extra_rules,
        )
        timeout_sec = int(confirmation.get("timeout_seconds") or 300)
        if confirmation.get("safety_invariant"):
            timeout_sec = max(timeout_sec, 24 * 3600)
        session.add(
            HumanTaskModel(
                id=task_id,
                goal_id=goal.id,
                work_id=None,
                run_id=None,
                task_type="DELIVERY_GAP_INTERVENE",
                prompt=message,
                requested_by=actor,
                due_at=datetime.now(UTC) + timedelta(seconds=max(timeout_sec, 60)),
                status="OPEN",
            )
        )
        metadata["pending_delivery_gap_human"] = {
            "human_task_id": str(task_id),
            "gap_kind": gap_kind,
            "gap_reasons": reasons,
            "attempts_tried": attempts,
            "draft_uri": draft_uri or None,
            "preview_endpoint": preview_endpoint or None,
        }
        goal.metadata_json = merge_live_action_into_metadata(
            metadata,
            "等待你确认以继续",
            stage="WAITING_HUMAN",
            event_type="DELIVERY_GAP_EXHAUSTED",
        )
        flag_modified(goal, "metadata_json")
        await DeliveryGapRecoveryService._append(
            session,
            project_id,
            role="ASSISTANT",
            message_type="DELIVERY_GAP_EXHAUSTED",
            content=task_summary,
            metadata={
                "goal_id": str(goal.id),
                "id": str(task_id),
                "human_task_id": str(task_id),
                "task_type": "DELIVERY_GAP_INTERVENE",
                "attempts": attempts,
                "gap_reasons": reasons,
                "gap_kind": gap_kind,
                "handoff": "WAITING_HUMAN",
                "prompt": message,
                "confirmation": confirmation,
            },
        )
        return DeliveryGapRecoveryResult(
            False,
            "STOP",
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
    ) -> DeliveryGapRecoveryResult:
        """After human authorizes continue: reset ladder counter and re-enter recover/replan.

        Chat「批准」must not only flip WAITING_HUMAN→ACTIVE (fake resume). Without this,
        delivery_gap_recovery_attempts stays exhausted and nothing regenerates.
        """
        gap_reasons: list[str] = []
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return DeliveryGapRecoveryResult(
                    False, "BLOCK", "goal not found", 0, terminal_exhaust=False
                )
            metadata = dict(goal.metadata_json or {})
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
            metadata["awaiting_human_intervention"] = False
            metadata.pop("termination", None)
            metadata.pop("pending_delivery_gap_human", None)
            metadata["execution_stage"] = "GENERATING"
            metadata["human_resume_nonce"] = (
                f"human:{datetime.now(UTC).isoformat()}:{uuid.uuid4().hex[:8]}"
            )
            goal.metadata_json = merge_live_action_into_metadata(
                metadata,
                "已批准，正在重新规划并继续生成",
                stage="GENERATING",
                event_type="ATTAINMENT_RECOVERY_STARTED",
            )
            flag_modified(goal, "metadata_json")

            req_id, plan_id = await self._resolve_generation_ids(session, goal_id)
            if req_id is None or plan_id is None:
                await self._append(
                    session,
                    project_id,
                    role="ASSISTANT",
                    message_type="ATTAINMENT_RECOVERY_STARTED",
                    content=(
                        "已批准，但缺少生成谱系（requirement/plan），无法自动重开交付恢复；"
                        "请补充方向或重新确认目标。"
                    ),
                    metadata={"goal_id": str(goal_id), "human_resume": True},
                )
                return DeliveryGapRecoveryResult(
                    False,
                    "BLOCK",
                    "missing generation lineage after human approve",
                    0,
                    str(metadata.get("delivery_gap_kind") or "product_surface"),
                )

        return await self.recover(
            goal_id=goal_id,
            project_id=project_id,
            requirement_revision_id=req_id,
            capability_resolution_plan_id=plan_id,
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

    @staticmethod
    async def _append(
        session: AsyncSession,
        project_id: uuid.UUID,
        *,
        role: str,
        message_type: str,
        content: str,
        metadata: dict[str, object],
    ) -> None:
        conversation = await session.scalar(
            select(ConversationModel).where(ConversationModel.app_project_id == project_id)
        )
        if conversation is None:
            return
        last = await session.scalar(
            select(ConversationMessageModel.ordinal)
            .where(ConversationMessageModel.conversation_id == conversation.id)
            .order_by(ConversationMessageModel.ordinal.desc())
            .limit(1)
        )
        session.add(
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                ordinal=(last or 0) + 1,
                role=role,
                message_type=message_type,
                content=content,
                metadata_json=metadata,
                created_by="regent-core",
            )
        )
