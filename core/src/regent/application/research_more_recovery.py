"""Recover RESEARCH_MORE by binding certified evidence connector capability.

Per REGENT-DEFINITION-3.0 ATTRIBUTE_1/5: the business goal sets direction, and
Evidence serves learning rather than gating exploration. Capability recovery orders
REUSE→…→request human last; exhausted auto-recovery must adapt and continue with
available external evidence — not block the console waiting for URL paste.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.capability_resolution_service import (
    CapabilityCandidate,
    CapabilityGap,
    CapabilityResolutionService,
    ResolutionMethod,
)
from regent.application.evidence_policy import (
    collect_authorized_urls,
    goal_requires_external_evidence,
)
from regent.application.execution_events import (
    DISCOVERY_ROUND_REQUESTED,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.infrastructure.evidence_capability import (
    CAPABILITY_NAME,
    ensure_allowlisted_http_capability,
    load_allowlisted_http_capability_package,
)
from regent.infrastructure.models import (
    CapabilityModel,
    ConversationMessageModel,
    ConversationModel,
    DiscoveryRoundModel,
    GoalModel,
    GoalSpecModel,
)

logger = logging.getLogger(__name__)

_MAX_AUTO_RECOVERY_ATTEMPTS = 2
_ADAPT_POLICY = "adapt_select_with_available_evidence"


@dataclass(frozen=True, slots=True)
class ResearchMoreRecoveryResult:
    recovered: bool
    method: str
    capability_id: uuid.UUID | None
    authorized_urls: tuple[str, ...]
    message: str


class ResearchMoreRecoveryService:
    """Core detects the gap; capability pool supplies the connector — not chat paste."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._resolver = CapabilityResolutionService()

    async def recover(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        round_id: uuid.UUID,
        actor: str,
    ) -> ResearchMoreRecoveryResult:
        capability_id = await ensure_allowlisted_http_capability(self._sessions)
        package = load_allowlisted_http_capability_package()

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return ResearchMoreRecoveryResult(
                    False, "BLOCK", None, (), "goal not found"
                )
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            if spec is None or spec.status != "FROZEN":
                return ResearchMoreRecoveryResult(
                    False, "BLOCK", None, (), "frozen goal spec required"
                )

            metadata = dict(goal.metadata_json or {})
            attempts = int(metadata.get("research_more_recovery_attempts") or 0)
            if attempts >= _MAX_AUTO_RECOVERY_ATTEMPTS:
                return await self._adapt_and_continue(
                    session,
                    goal=goal,
                    spec=spec,
                    project_id=project_id,
                    round_id=round_id,
                    actor=actor,
                    capability_id=capability_id,
                    package_feeds=list(package.default_feeds),
                    attempts=attempts,
                )

            capability = await session.scalar(
                select(CapabilityModel).where(CapabilityModel.id == capability_id)
            )
            candidates = [
                CapabilityCandidate(
                    id=capability_id,
                    name=CAPABILITY_NAME,
                    status=capability.status if capability else "VERIFIED",
                )
            ]
            plan = self._resolver.resolve(
                [
                    CapabilityGap(
                        requirement_key="evidence.http_snapshot",
                        capability_name=CAPABILITY_NAME,
                        build_allowed=False,
                        human_resolvable=True,
                    )
                ],
                candidates,
                [],
            )
            item = plan.items[0]
            if item.method is not ResolutionMethod.REUSE:
                # Still do not park on human paste: adapt with whatever URLs exist.
                return await self._adapt_and_continue(
                    session,
                    goal=goal,
                    spec=spec,
                    project_id=project_id,
                    round_id=round_id,
                    actor=actor,
                    capability_id=capability_id,
                    package_feeds=list(package.default_feeds),
                    attempts=attempts,
                    note=(
                        f"能力池未能 REUSE {CAPABILITY_NAME} "
                        f"(method={item.method.value}); 改为自适应推进。"
                    ),
                )

            merged = self._merge_urls(goal, spec, metadata, list(package.default_feeds))
            if not merged:
                return await self._adapt_and_continue(
                    session,
                    goal=goal,
                    spec=spec,
                    project_id=project_id,
                    round_id=round_id,
                    actor=actor,
                    capability_id=capability_id,
                    package_feeds=list(package.default_feeds),
                    attempts=attempts,
                    note="无授权 URL 可抓取; 在已有 goal-intent 证据下自适应推进。",
                )

            return await self._resume_discovery(
                session,
                goal=goal,
                spec=spec,
                project_id=project_id,
                round_id=round_id,
                actor=actor,
                capability_id=capability_id,
                merged=merged,
                attempts=attempts,
                method="REUSE",
                discovery_policy=None,
                message=(
                    f"已发现证据能力缺口并 REUSE 认证能力 {CAPABILITY_NAME}。"
                    "正在用能力包默认源与 Goal 授权 URL 重新取证并发现。"
                ),
                package_feeds=list(package.default_feeds),
            )

    async def _adapt_and_continue(
        self,
        session: AsyncSession,
        *,
        goal: GoalModel,
        spec: GoalSpecModel,
        project_id: uuid.UUID,
        round_id: uuid.UUID,
        actor: str,
        capability_id: uuid.UUID,
        package_feeds: list[str],
        attempts: int,
        note: str | None = None,
    ) -> ResearchMoreRecoveryResult:
        """DEFINITION: do not block Goal progress waiting for optional human URL paste."""
        metadata = dict(goal.metadata_json or {})
        existing_urls = [
            str(u).strip()
            for u in (metadata.get("authorized_source_urls") or [])
            if str(u).strip()
        ]
        if metadata.get("research_more_adapted") and existing_urls:
            # Already adapted AND have URLs but still no evidence — need human, not calm EXHAUST.
            metadata["execution_stage"] = "WAITING_HUMAN"
            metadata["awaiting_authorized_sources"] = True
            metadata["awaiting_human_intervention"] = True
            metadata["termination"] = {
                "reason": "research_more_needs_human",
                # 3.0 ATTRIBUTE_8: reality contact stays accountable and the legal
                # subject keeps the non-transferable takeover right.
                "definition": "REGENT-DEFINITION-3.0 ATTRIBUTE_8",
                "handoff": "WAITING_HUMAN",
            }
            goal.metadata_json = metadata
            message = (
                "自适应发现仍无法获得足够外部证据以继续交付。"
                "已尝试能力 REUSE 与默认源；需要你补充授权来源或方向后继续，"
                "不会标记为已完成。"
            )
            await self._append(
                session,
                project_id,
                role="ASSISTANT",
                message_type="RESEARCH_MORE_ADAPT_EXHAUSTED",
                content=message,
                metadata={
                    "goal_id": str(goal.id),
                    "attempts": attempts,
                    "handoff": "WAITING_HUMAN",
                },
            )
            return ResearchMoreRecoveryResult(
                False, "STOP", capability_id, (), message
            )

        # If authorized_source_urls is empty but we have package_feeds,
        # use the package feeds — don't give up without trying them.
        if not existing_urls and package_feeds:
            merged = list(dict.fromkeys(package_feeds))
            logger.info(
                "authorized_source_urls empty; falling back to %d package feeds",
                len(merged),
                extra={"goal_id": str(goal.id)},
            )
        else:
            merged = self._merge_urls(goal, spec, metadata, package_feeds)
        prefix = note or (
            "自动能力恢复已达轮次上限。按 REGENT-DEFINITION-3.0: "
            "证据用于学习而非探索许可; 不以等人粘贴 URL 为默认路径, "
            "改用已有外部证据自适应缩小范围并继续推进。"
        )
        return await self._resume_discovery(
            session,
            goal=goal,
            spec=spec,
            project_id=project_id,
            round_id=round_id,
            actor=actor,
            capability_id=capability_id,
            merged=merged,
            attempts=attempts,
            method="ADAPT_CONTINUE",
            discovery_policy=_ADAPT_POLICY,
            message=prefix,
            package_feeds=package_feeds,
            adapted=True,
        )

    async def _resume_discovery(
        self,
        session: AsyncSession,
        *,
        goal: GoalModel,
        spec: GoalSpecModel,
        project_id: uuid.UUID,
        round_id: uuid.UUID,
        actor: str,
        capability_id: uuid.UUID,
        merged: list[str],
        attempts: int,
        method: str,
        discovery_policy: str | None,
        message: str,
        package_feeds: list[str],
        adapted: bool = False,
    ) -> ResearchMoreRecoveryResult:
        metadata = dict(goal.metadata_json or {})
        metadata["authorized_source_urls"] = merged
        metadata["awaiting_authorized_sources"] = False
        metadata["execution_stage"] = "DISCOVERING"
        metadata["research_more_recovery_attempts"] = attempts + 1
        if adapted:
            metadata["research_more_adapted"] = True
        if discovery_policy:
            metadata["discovery_policy"] = discovery_policy
        metadata["capability_resolution"] = {
            "method": method,
            "capability_name": CAPABILITY_NAME,
            "capability_id": str(capability_id),
            "bound_from_round_id": str(round_id),
            "default_feeds_from_capability": package_feeds,
            "discovery_policy": discovery_policy,
        }
        goal.metadata_json = metadata

        resume_key = make_idempotency_key(
            "discovery-capability-resume",
            goal.id,
            f"{round_id}:{attempts + 1}:{method}",
        )
        next_round = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(DiscoveryRoundModel.round), 0)).where(
                        DiscoveryRoundModel.goal_id == goal.id
                    )
                )
                or 0
            )
            + 1
        )
        snapshot = {
            "goal_id": str(goal.id),
            "goal_version": goal.version,
            "spec_version": spec.version,
            "constraints": spec.explicit_constraints,
            "success_criteria": spec.success_criteria,
            "authorized_source_urls": merged,
            "resume_of": "RESEARCH_MORE",
            "capability_id": str(capability_id),
            "discovery_policy": discovery_policy,
            "method": method,
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(
                snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        discovery_round = DiscoveryRoundModel(
            id=uuid.uuid4(),
            goal_id=goal.id,
            round=next_round,
            status="REQUESTED",
            version=0,
            input_snapshot_hash=snapshot_hash,
            budget={"max_sources": 8, "max_tokens": 50_000},
            policy_version="discovery-v1",
            idempotency_key=resume_key,
            created_by=actor,
            correlation_id=str(goal.correlation_id),
        )
        session.add(discovery_round)
        session.add(
            make_outbox_event(
                EventEnvelope(
                    event_type=DISCOVERY_ROUND_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal.id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal.id),
                        "app_project_id": str(project_id),
                        "discovery_round_id": str(discovery_round.id),
                        "round": next_round,
                        "actor": actor,
                        "idempotency_key": resume_key,
                        "resume_of": "RESEARCH_MORE",
                        "capability_id": str(capability_id),
                        "discovery_policy": discovery_policy,
                        "method": method,
                    },
                    idempotency_key=resume_key,
                    correlation_id=goal.correlation_id,
                )
            )
        )
        full_message = f"{message} (round {next_round})."
        await self._append(
            session,
            project_id,
            role="ASSISTANT",
            message_type=(
                "RESEARCH_MORE_ADAPT_CONTINUE"
                if adapted
                else "RESEARCH_MORE_CAPABILITY_REUSED"
            ),
            content=full_message,
            metadata={
                "goal_id": str(goal.id),
                "capability_id": str(capability_id),
                "discovery_round_id": str(discovery_round.id),
                "round": next_round,
                "authorized_source_urls": merged[:12],
                "method": method,
                "discovery_policy": discovery_policy,
            },
        )
        await self._append(
            session,
            project_id,
            role="EVENT",
            message_type="DISCOVERY_ROUND_REQUESTED",
            content=(
                f"Core continued discovery round {next_round} via {method} "
                "(goal-driven; not waiting for human approval)."
            ),
            metadata={
                "goal_id": str(goal.id),
                "discovery_round_id": str(discovery_round.id),
                "round": next_round,
                "capability_id": str(capability_id),
                "method": method,
            },
        )
        logger.info(
            "research_more continued",
            extra={
                "goal_id": str(goal.id),
                "capability_id": str(capability_id),
                "round": next_round,
                "method": method,
                "url_count": len(merged),
            },
        )
        return ResearchMoreRecoveryResult(
            True, method, capability_id, tuple(merged), full_message
        )

    @staticmethod
    def _merge_urls(
        goal: GoalModel,
        spec: GoalSpecModel,
        metadata: dict[str, Any],
        package_feeds: list[str],
    ) -> list[str]:
        goal_urls = collect_authorized_urls(
            goal.original_input or "", dict(spec.explicit_constraints or {})
        )
        existing = [
            str(u).strip()
            for u in (metadata.get("authorized_source_urls") or [])
            if str(u).strip()
        ]
        needs_external = goal_requires_external_evidence(
            goal.original_input or "", dict(spec.explicit_constraints or {})
        )
        capability_feeds = list(package_feeds) if needs_external else []
        return list(dict.fromkeys([*existing, *goal_urls, *capability_feeds]))

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
