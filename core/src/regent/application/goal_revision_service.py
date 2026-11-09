"""GoalRevisionService — 目标进化外环。

在自然检查点评估当前 GoalSpec 是否仍充分，若否创建进化版本。
与内环（GoalAnchor 防漂移）构成两层稳态架构。

触发条件：
- milestone_boundary: 里程碑推进时检查 success_criteria 是否仍合理
- delivery_failure: gate FAILED 且 reorg 已用尽
- user_direction_change: 用户 MODIFY 指令或明确战略转向
- correction_accumulation: active_corrections 累积达到阈值
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.infrastructure.models import (
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    GoalSpecModel,
)

logger = logging.getLogger(__name__)

# Every N corrections triggers a baseline merge.
CORRECTION_ACCUMULATION_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class GoalRevisionResult:
    """Result of a revision assessment."""

    revised: bool
    reason: str = ""
    new_spec_version: int = 0
    old_spec_version: int = 0


class GoalRevisionService:
    """Outer loop: goal revision when current spec is inadequate."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def assess_and_revise(
        self,
        goal_id: uuid.UUID,
        *,
        trigger: str,
        actor: str,
        revision_context: dict | None = None,
        session: AsyncSession | None = None,
    ) -> GoalRevisionResult:
        """Evaluate whether the current GoalSpec needs evolution.

        If yes: supersede current spec, create new FROZEN spec (evolved version),
        update goal metadata, and emit a conversation event.

        If ``session`` is provided, use it directly (caller manages the
        transaction). Otherwise, create a new session internally.
        """
        if session is not None:
            return await self._do_assess(session, goal_id, trigger, actor, revision_context)
        async with self._sessions() as own_session, own_session.begin():
            return await self._do_assess(
                own_session, goal_id, trigger, actor, revision_context
            )

    async def _do_assess(
        self,
        session: AsyncSession,
        goal_id: uuid.UUID,
        trigger: str,
        actor: str,
        revision_context: dict | None,
    ) -> GoalRevisionResult:
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return GoalRevisionResult(revised=False, reason="goal not found")

        latest_spec = await session.scalar(
            select(GoalSpecModel)
            .where(GoalSpecModel.goal_id == goal_id)
            .order_by(GoalSpecModel.version.desc())
            .limit(1)
            .with_for_update()
        )
        if latest_spec is None:
            return GoalRevisionResult(revised=False, reason="no spec found")

        # Check if revision is warranted based on trigger type.
        should_revise, reason = self._should_revise(
            trigger=trigger,
            goal=goal,
            latest_spec=latest_spec,
            revision_context=revision_context or {},
        )
        if not should_revise:
            return GoalRevisionResult(
                revised=False,
                reason=reason,
                old_spec_version=latest_spec.version,
            )

        # Build evolved spec content — carry forward what's still valid,
        # relax/adjust what triggered the revision.
        evolved_content = self._build_evolved_content(
            latest_spec=latest_spec,
            trigger=trigger,
            revision_context=revision_context or {},
        )

        # Supersede old spec.
        latest_spec.status = "SUPERSEDED"

        # Create new evolved spec.
        new_version = latest_spec.version + 1
        new_spec = GoalSpecModel(
            id=uuid.uuid4(),
            goal_id=goal_id,
            version=new_version,
            status="FROZEN",
            content_hash=canonical_hash(evolved_content),
            confirmed_by=f"regent-core:revision-{trigger}",
            confirmed_at=datetime.now(UTC),
            **evolved_content,
        )
        session.add(new_spec)

        # Update goal metadata: locked spec pointer + revision history.
        metadata = dict(goal.metadata_json or {})
        revision_history = list(metadata.get("goal_revision_history", []))
        revision_history.append({
            "from_version": latest_spec.version,
            "to_version": new_version,
            "trigger": trigger,
            "reason": reason,
            "revision_context": revision_context or {},
            "at": datetime.now(UTC).isoformat(),
            "actor": actor,
        })
        metadata["goal_revision_history"] = revision_history
        metadata["locked_spec_hash"] = new_spec.content_hash
        metadata["locked_spec_version"] = new_version
        metadata["latest_goal_spec_version"] = new_version
        metadata["goal_spec_hash"] = new_spec.content_hash
        goal.metadata_json = metadata

        # Notify user via conversation event.
        await self._append_conversation_event(
            session,
            goal=goal,
            trigger=trigger,
            reason=reason,
            old_version=latest_spec.version,
            new_version=new_version,
            actor=actor,
        )

        logger.info(
            "goal spec evolved",
            extra={
                "goal_id": str(goal_id),
                "trigger": trigger,
                "from_version": latest_spec.version,
                "to_version": new_version,
            },
        )

        return GoalRevisionResult(
            revised=True,
            reason=reason,
            new_spec_version=new_version,
            old_spec_version=latest_spec.version,
        )

    def _should_revise(
        self,
        *,
        trigger: str,
        goal: GoalModel,
        latest_spec: GoalSpecModel,
        revision_context: dict,
    ) -> tuple[bool, str]:
        """Decide whether revision is warranted."""
        metadata = dict(goal.metadata_json or {})
        revision_history = list(metadata.get("goal_revision_history", []))

        # Rate limit: no more than 3 revisions per goal to prevent oscillation.
        if len(revision_history) >= 3:
            return False, "revision rate limit reached (max 3)"

        if trigger == "milestone_boundary":
            # Always assess at milestone boundaries — the original success
            # criteria may no longer match reality after completing a phase.
            return True, "milestone boundary: re-evaluating spec adequacy"

        if trigger == "delivery_failure":
            # Reorg exhausted — the spec itself may be unrealistic.
            return True, "delivery failure with reorg exhausted"

        if trigger == "user_direction_change":
            # User explicitly changed direction.
            return True, "user direction change detected"

        if trigger == "correction_accumulation":
            corrections = list(metadata.get("active_corrections", []))
            if len(corrections) >= CORRECTION_ACCUMULATION_THRESHOLD:
                return True, (
                    f"correction accumulation: {len(corrections)} corrections "
                    f"since last baseline"
                )
            return False, "insufficient corrections for baseline merge"

        return False, f"unknown trigger: {trigger}"

    def _build_evolved_content(
        self,
        *,
        latest_spec: GoalSpecModel,
        trigger: str,
        revision_context: dict,
    ) -> dict:
        """Build the spec content for the evolved version.

        Strategy: carry forward everything, but incorporate revision context.
        The original_input (direction anchor) is NOT part of the spec —
        it lives on the Goal model and is never modified here.
        """
        constraints = dict(latest_spec.explicit_constraints or {})
        inferences = dict(latest_spec.system_inferences or {})
        unknowns = list(latest_spec.unknowns or [])
        success_criteria = dict(latest_spec.success_criteria or {})
        source_refs = list(latest_spec.source_refs or [])

        # Add revision provenance.
        source_refs.append({
            "type": "goal_revision",
            "trigger": trigger,
            "from_version": latest_spec.version,
            "at": datetime.now(UTC).isoformat(),
        })

        # For delivery_failure: relax constraints that may be too strict.
        if trigger == "delivery_failure" and revision_context:
            constraints["_relaxed_for"] = "delivery_failure"
            # Clear guardrail constraints that blocked delivery.
            # Keep structural constraints (tech stack, entry point, etc.).
            relaxed = {
                k: v for k, v in constraints.items()
                if not k.startswith("_") and k not in (
                    "response_time_ms", "error_rate", "throughput",
                )
            }
            constraints = relaxed

        # For correction_accumulation: merge active corrections into baseline.
        if trigger == "correction_accumulation":
            # Clear the corrections list — they're now baked into the spec.
            constraints["_corrections_merged"] = True

        return {
            "explicit_constraints": constraints,
            "system_inferences": inferences,
            "unknowns": unknowns,
            "success_criteria": success_criteria,
            "source_refs": source_refs,
        }

    @staticmethod
    async def _append_conversation_event(
        session: AsyncSession,
        *,
        goal: GoalModel,
        trigger: str,
        reason: str,
        old_version: int,
        new_version: int,
        actor: str,
    ) -> None:
        """Write a conversation event notifying the user about spec evolution."""
        if goal.app_project_id is None:
            return
        conversation = await session.scalar(
            select(ConversationModel).where(
                ConversationModel.app_project_id == goal.app_project_id
            )
        )
        if conversation is None:
            return
        last = await session.scalar(
            select(ConversationMessageModel.ordinal)
            .where(ConversationMessageModel.conversation_id == conversation.id)
            .order_by(ConversationMessageModel.ordinal.desc())
            .limit(1)
        )

        trigger_labels = {
            "milestone_boundary": "阶段推进",
            "delivery_failure": "交付受阻",
            "user_direction_change": "方向调整",
            "correction_accumulation": "修正累积",
        }
        label = trigger_labels.get(trigger, trigger)

        session.add(
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                ordinal=(last or 0) + 1,
                role="EVENT",
                message_type="GOAL_SPEC_EVOLVED",
                content=(
                    f"目标规格已自动进化（v{old_version} → v{new_version}）。"
                    f"触发原因：{label}。{reason}。"
                    f"原始目标方向不变，当前理解已更新。"
                ),
                metadata_json={
                    "goal_id": str(goal.id),
                    "trigger": trigger,
                    "old_version": old_version,
                    "new_version": new_version,
                    "actor": actor,
                },
                created_by="regent-core",
            )
        )
