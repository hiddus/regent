"""Watch ACTIVE goals for stalled live_action and force a human-visible path."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.confirmation_present import confirmation_for_human_task
from regent.application.live_action import build_live_action
from regent.infrastructure.models import (
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    HumanTaskModel,
)

logger = logging.getLogger(__name__)

_STALE_WARN_MINUTES = 5
_STALE_HANDOFF_MINUTES = 15
# Cluster-wide lock so multi-worker fleets do not stampede handoffs.
_ADVISORY_LOCK_KEY = 87201401


def _parse_updated_at(live: dict[str, Any] | None) -> datetime | None:
    if not isinstance(live, dict):
        return None
    raw = live.get("updated_at")
    if not raw:
        return None
    try:
        text_value = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


async def _append_handoff_message(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    goal_id: uuid.UUID,
    task_id: uuid.UUID,
    prompt: str,
    confirmation: dict[str, Any],
    stale_minutes: int,
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
            role="ASSISTANT",
            message_type="HUMAN_TASK_REQUIRED",
            content="长时间无进展，需要你确认如何继续",
            metadata_json={
                "goal_id": str(goal_id),
                "id": str(task_id),
                "human_task_id": str(task_id),
                "task_type": "DELIVERY_GAP_INTERVENE",
                "stage": "STALE_PROGRESS_HANDOFF",
                "handoff": "WAITING_HUMAN",
                "prompt": prompt,
                "confirmation": confirmation,
                "stale_for_minutes": stale_minutes,
            },
            created_by="regent-core:stale-progress",
        )
    )


async def tick_stale_delivery_progress(
    sessions: async_sessionmaker[AsyncSession],
    *,
    limit: int = 40,
) -> dict[str, int]:
    """5min: refresh live_action warning; 15min: open a retry HumanTask if none."""
    stats = {"warned": 0, "handed_off": 0, "skipped_lock": 0}
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        locked = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"),
            {"k": _ADVISORY_LOCK_KEY},
        )
        if not locked:
            stats["skipped_lock"] = 1
            return stats
        goals = list(
            (
                await session.execute(
                    select(GoalModel)
                    .where(GoalModel.status == "ACTIVE")
                    .order_by(GoalModel.updated_at.asc())
                    .limit(limit)
                )
            ).scalars().all()
        )
        for goal in goals:
            meta = dict(goal.metadata_json or {})
            live = meta.get("live_action")
            updated = _parse_updated_at(live if isinstance(live, dict) else None)
            if updated is None:
                # Fall back to goal.updated_at when live_action never set.
                updated = goal.updated_at
                if updated is not None and updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
            if updated is None:
                continue
            age = now - updated
            if age < timedelta(minutes=_STALE_WARN_MINUTES):
                continue

            stale_minutes = int(age.total_seconds() // 60)
            if age >= timedelta(minutes=_STALE_HANDOFF_MINUTES):
                open_task = await session.scalar(
                    select(HumanTaskModel.id).where(
                        HumanTaskModel.goal_id == goal.id,
                        HumanTaskModel.status == "OPEN",
                    )
                )
                if open_task is None and not meta.get("stale_progress_handoff_at"):
                    task_id = uuid.uuid4()
                    prompt = (
                        "创建后长时间无进展。请选择缩小范围、继续尝试，或停止本目标。"
                    )
                    confirmation = confirmation_for_human_task(
                        task_type="DELIVERY_GAP_INTERVENE",
                        summary="长时间无进展，需要你确认如何继续",
                        rationale="系统已超过 15 分钟没有可感知进展",
                        detail=f"stale_for_minutes={stale_minutes}",
                        prompt=prompt,
                        extra_rules=["stage:STALE_PROGRESS_HANDOFF"],
                    )
                    timeout_sec = int(confirmation.get("timeout_seconds") or 300)
                    session.add(
                        HumanTaskModel(
                            id=task_id,
                            goal_id=goal.id,
                            work_id=None,
                            run_id=None,
                            task_type="DELIVERY_GAP_INTERVENE",
                            prompt=prompt,
                            requested_by="regent-core:stale-progress",
                            due_at=now + timedelta(seconds=max(timeout_sec, 60)),
                            status="OPEN",
                        )
                    )
                    meta["stale_progress_handoff_at"] = now.isoformat()
                    meta["awaiting_human_intervention"] = True
                    meta["pending_delivery_gap_human"] = {
                        "human_task_id": str(task_id),
                        "gap_kind": "stale-progress",
                        "gap_reasons": [f"stale-progress:{stale_minutes}m"],
                    }
                    meta["live_action"] = build_live_action(
                        "长时间无进展，等待你确认后继续（不会自动拒绝）",
                        stage="WAITING_HUMAN",
                        event_type="STALE_PROGRESS_HANDOFF",
                        detail=f"stale_for_minutes={stale_minutes}",
                    )
                    goal.metadata_json = meta
                    flag_modified(goal, "metadata_json")
                    if goal.app_project_id is not None:
                        await _append_handoff_message(
                            session,
                            project_id=goal.app_project_id,
                            goal_id=goal.id,
                            task_id=task_id,
                            prompt=prompt,
                            confirmation=confirmation,
                            stale_minutes=stale_minutes,
                        )
                    stats["handed_off"] += 1
                    logger.warning(
                        "stale progress handoff",
                        extra={"goal_id": str(goal.id), "age_min": stale_minutes},
                    )
                continue

            # 5–15 min: warn via live_action without creating a task yet.
            if meta.get("stale_progress_warned_at"):
                continue
            meta["stale_progress_warned_at"] = now.isoformat()
            meta["live_action"] = build_live_action(
                "仍在处理，但已较久无新进展…",
                stage=str(meta.get("execution_stage") or "ACTIVE"),
                event_type="STALE_PROGRESS_WARN",
                detail=f"stale_for_minutes={stale_minutes}",
            )
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")
            stats["warned"] += 1
    return stats
