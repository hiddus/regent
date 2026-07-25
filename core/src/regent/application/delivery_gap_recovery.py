"""Recover delivery/goal-attainment gaps by organizing more capability — not shipping junk.

Per REGENT-DEFINITION-1.0:
- ATTRIBUTE_3: discover capability gaps; REUSE→CONFIGURE→COMPOSE→BUILD→request human last
- ATTRIBUTE_4: organization is a means; grow agents/capabilities when needed
- ATTRIBUTE_6: external outcome loop — do not treat generator self-score as Goal success
- ATTRIBUTE_7: explicit termination when Goal cannot be attained

GAC-D: escalate the ladder across attempts, reorganize the goal org, BUILD real packages.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.capability_acquire_service import (
    AcquireRequest,
    CapabilityAcquireService,
)
from regent.application.capability_build_service import build_attainment_capability
from regent.application.capability_ladder import (
    MAX_ATTAINMENT_ESCALATION_ATTEMPTS,
    EscalationStep,
    built_capability_name,
    composed_capability_name,
    plan_escalation,
)
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
from regent.application.organization_service import OrganizationService
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
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    GoalSpecModel,
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
    "outbound",
    "observed",
    "http",
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


class DeliveryGapRecoveryService:
    """Escalate capabilities + reorganize agents when delivery does not attain Goal."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._resolver = CapabilityResolutionService()
        self._orgs = OrganizationService(sessions)

    async def recover(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        requirement_revision_id: uuid.UUID,
        capability_resolution_plan_id: uuid.UUID,
        actor: str,
        gap_reasons: list[str],
    ) -> DeliveryGapRecoveryResult:
        surface_id = await ensure_product_surface_capability(self._sessions)
        review_id = await ensure_delivery_review_capability(self._sessions)
        http_id = await ensure_allowlisted_http_capability(self._sessions)
        reasons = [str(r) for r in gap_reasons if str(r).strip()][:12]
        gap_kind = classify_delivery_gap_kind(reasons)
        guidance = guidance_for_gap_kind(gap_kind)

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
            attempts = int(metadata.get("delivery_gap_recovery_attempts") or 0)
            plan = plan_escalation(attempts)

            if plan.exhausted or plan.step is EscalationStep.STOP:
                metadata["execution_stage"] = "BLOCKED"
                metadata["awaiting_authorized_sources"] = False
                metadata["delivery_gap_kind"] = gap_kind
                metadata["termination"] = {
                    "reason": "goal_attainment_not_reached",
                    "definition": "REGENT-DEFINITION-1.0 ATTRIBUTE_7",
                    "gap_reasons": reasons,
                    "gap_kind": gap_kind,
                    "ladder_exhausted": True,
                    "gac": "GAC-D1",
                }
                goal.metadata_json = metadata
                message = (
                    "交付未达成 Goal。已按 ATTRIBUTE_3 爬完 REUSE→COMPOSE→BUILD；"
                    "拒绝发布不可靠表面，进入有证据终态（ATTRIBUTE_7）。"
                )
                await self._append(
                    session,
                    project_id,
                    role="ASSISTANT",
                    message_type="DELIVERY_GAP_EXHAUSTED",
                    content=message,
                    metadata={
                        "goal_id": str(goal_id),
                        "attempts": attempts,
                        "gap_reasons": reasons,
                        "gap_kind": gap_kind,
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
            all_guidance = list(dict.fromkeys([*extra_guidance, *guidance]))

            reorg = await self._orgs.reorganize_for_gap(
                session,
                goal_id=goal_id,
                gap_kind=gap_kind,
                method=method,
                capability_names=[
                    primary_name,
                    PRODUCT_SURFACE_NAME,
                    HTTP_SOURCE_NAME,
                    DELIVERY_REVIEW_NAME,
                ],
                attempt=plan.attempt,
                actor=actor,
            )

            metadata["delivery_gap_recovery_attempts"] = plan.attempt
            metadata["delivery_policy"] = _DELIVERY_POLICY
            metadata["delivery_gap_reasons"] = reasons
            metadata["delivery_gap_kind"] = gap_kind
            metadata["execution_stage"] = "GENERATING"
            metadata["awaiting_authorized_sources"] = False
            metadata["organization_id"] = str(reorg.receipt.organization_id)
            metadata["organization_strategy"] = reorg.receipt.strategy
            metadata["capability_resolution"] = {
                **dict(metadata.get("capability_resolution") or {}),
                "delivery_method": method,
                "escalation_step": plan.step.value,
                "delivery_gap_kind": gap_kind,
                "primary_capability": primary_name,
                "primary_capability_id": str(primary_id),
                "product_surface_capability_id": str(surface_id),
                "delivery_review_capability_id": str(review_id),
                "allowlisted_http_capability_id": str(http_id),
                "recovery_work_id": str(reorg.recovery_work_id),
                "organization_id": str(reorg.receipt.organization_id),
                "generation_guidance": all_guidance,
            }
            goal.metadata_json = metadata

            resume_key = make_idempotency_key(
                "generation-delivery-recovery",
                goal.id,
                f"{requirement_revision_id}:{plan.attempt}:{gap_kind}:{method}",
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
                            "delivery_gap_recovery_attempt": plan.attempt,
                            "delivery_gap_kind": gap_kind,
                            "escalation_step": method,
                            "gap_reasons": reasons,
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
                f"ATTRIBUTE_3 {method} → {primary_name}；ATTRIBUTE_4 重组组织 "
                f"{reorg.receipt.strategy}（attempt {plan.attempt}/"
                f"{MAX_ATTAINMENT_ESCALATION_ATTEMPTS}）。"
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
                    "attempt": plan.attempt,
                    "method": method,
                    "gap_reasons": reasons,
                    "gap_kind": gap_kind,
                    "capability_id": str(primary_id),
                    "capability_name": primary_name,
                    "organization_id": str(reorg.receipt.organization_id),
                    "recovery_work_id": str(reorg.recovery_work_id),
                },
            )
            logger.info(
                "delivery gap escalated",
                extra={
                    "goal_id": str(goal.id),
                    "attempt": plan.attempt,
                    "method": method,
                    "gap_kind": gap_kind,
                    "org": str(reorg.receipt.organization_id),
                },
            )
            return DeliveryGapRecoveryResult(
                True,
                method,
                message,
                plan.attempt,
                gap_kind,
                recovery_work_id=reorg.recovery_work_id,
                organization_id=reorg.receipt.organization_id,
            )

    async def prepare_gate_reorganization(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        actor: str,
        gate_status: str,
    ) -> DeliveryGapRecoveryResult:
        """GAC-D4: before EXHAUST on gate failure, escalate capability + org once more."""
        surface_id = await ensure_product_surface_capability(self._sessions)
        review_id = await ensure_delivery_review_capability(self._sessions)
        http_id = await ensure_allowlisted_http_capability(self._sessions)
        gap_kind = "gate_failed"
        guidance = guidance_for_gap_kind(gap_kind)

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return DeliveryGapRecoveryResult(
                    False, "BLOCK", "goal not found", 0, gap_kind
                )
            metadata = dict(goal.metadata_json or {})
            attempts = int(metadata.get("gate_reorg_attempts") or 0)
            # Gate path allows up to COMPOSE then BUILD (2 rounds).
            if attempts >= 2:
                return DeliveryGapRecoveryResult(
                    False,
                    "STOP",
                    "gate reorganization exhausted",
                    attempts,
                    gap_kind,
                    terminal_exhaust=True,
                )
            plan = plan_escalation(attempts)
            # Prefer COMPOSE on first gate reorg, BUILD on second.
            step = EscalationStep.COMPOSE if attempts == 0 else EscalationStep.BUILD
            if plan.exhausted:
                step = EscalationStep.STOP
            if step is EscalationStep.STOP:
                return DeliveryGapRecoveryResult(
                    False, "STOP", "ladder exhausted", attempts, gap_kind, True
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
                step=step,
                gap_kind=gap_kind,
                guidance=guidance,
                reasons=[f"gate:{gate_status}"],
                candidates=candidates,
                surface_id=surface_id,
                http_id=http_id,
                review_id=review_id,
            )
            next_attempt = attempts + 1
            reorg = await self._orgs.reorganize_for_gap(
                session,
                goal_id=goal_id,
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
                "generation_guidance": list(
                    dict.fromkeys([*extra_guidance, *guidance])
                ),
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
                    "goal_id": str(goal_id),
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
    ) -> tuple[str, str, uuid.UUID, list[str]]:
        """Return (method, primary_name, primary_id, extra_guidance)."""
        if step is EscalationStep.REUSE:
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

        # ACQUIRE: attempt to fetch capability from network
        acquire_name = f"acquired-{gap_kind}-v1"
        acquire_svc = CapabilityAcquireService(self._sessions)
        result = await acquire_svc.acquire(
            AcquireRequest(
                capability_name=acquire_name,
                requirement_key=f"delivery.acquire.{gap_kind}",
                goal_id=goal_id,
            )
        )
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
