"""Watch ACTIVE goals for stalled live_action and auto-continue (no permission card).

Product rule: humans only for permission / danger. Stale progress is not a
permission gate — nudge the pipeline instead of minting DELIVERY_GAP_INTERVENE.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.execution_events import (
    EventEnvelope,
    GOAL_EXECUTION_REQUESTED,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.live_action import build_live_action
from regent.infrastructure.models import (
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    HumanTaskModel,
)

logger = logging.getLogger(__name__)

_STALE_WARN_MINUTES = 5
_STALE_NUDGE_MINUTES = 12
_STALE_NUDGE_COOLDOWN_MINUTES = 12
# Ship-first: stop fake-alive sooner — soft-pause after a few nudges.
_STALE_MAX_NUDGES = 2
# Cluster-wide lock so multi-worker fleets do not stampede nudges.
_ADVISORY_LOCK_KEY = 87201401

# Real permission / danger task types — never auto-cancel or nudge over these.
_PERMISSION_TASK_TYPES = frozenset(
    {
        "RELEASE_APPROVAL",
        "QUALITY_APPROVAL",
        "EXTERNAL_EFFECT",
        "PERMIT_REQUEST",
        "PERMIT_GRANT",
        "GOAL_CONFIRM",
    }
)


def _parse_updated_at(live: dict[str, Any] | None) -> datetime | None:
    if not isinstance(live, dict):
        return None
    raw = live.get("updated_at")
    if not raw:
        return None
    try:
        text_value = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text_value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        text_value = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text_value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _append_soft_note(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    goal_id: uuid.UUID,
    content: str,
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
            message_type="STALE_PROGRESS_NOTE",
            content=content,
            metadata_json={
                "goal_id": str(goal_id),
                "stage": "STALE_PROGRESS",
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
    """5min: live_action warn; 15min+: auto-nudge pipeline (no HumanTask)."""
    stats = {
        "warned": 0,
        "auto_continued": 0,
        "soft_noted": 0,
        "skipped_permission": 0,
        "skipped_lock": 0,
        # legacy key kept so older dashboards/tests don't KeyError
        "handed_off": 0,
    }
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
            # Already soft-paused or quiet-stopped — do not fake-nudge.
            if (
                str(meta.get("execution_stage") or "") == "DELIVERY_SOFT_PAUSE"
                or meta.get("ops_soft_pause")
            ):
                continue
            live = meta.get("live_action")
            updated = _parse_updated_at(live if isinstance(live, dict) else None)
            if updated is None:
                updated = goal.updated_at
                if updated is not None and updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
            if updated is None:
                continue
            age = now - updated
            if age < timedelta(minutes=_STALE_WARN_MINUTES):
                continue

            stale_minutes = int(age.total_seconds() // 60)
            if age >= timedelta(minutes=_STALE_NUDGE_MINUTES):
                open_tasks = list(
                    (
                        await session.execute(
                            select(HumanTaskModel).where(
                                HumanTaskModel.goal_id == goal.id,
                                HumanTaskModel.status == "OPEN",
                            )
                        )
                    ).scalars().all()
                )
                if any(
                    str(t.task_type or "").upper() in _PERMISSION_TASK_TYPES
                    for t in open_tasks
                ):
                    stats["skipped_permission"] += 1
                    continue

                # Cancel leftover non-permission intervene cards so they cannot loop.
                for task in open_tasks:
                    if str(task.task_type or "").upper() == "DELIVERY_GAP_INTERVENE":
                        task.status = "CANCELLED"
                        task.response = {
                            "cancelled": True,
                            "reason": "stale_progress_auto_continue",
                            "at": now.isoformat(),
                        }
                        task.completed_at = now

                last_nudge = _parse_iso(meta.get("stale_progress_nudged_at"))
                if last_nudge is not None and now - last_nudge < timedelta(
                    minutes=_STALE_NUDGE_COOLDOWN_MINUTES
                ):
                    continue

                nudge_count = int(meta.get("stale_progress_nudge_count") or 0)
                if nudge_count >= _STALE_MAX_NUDGES:
                    # Ship-first: reclaim fake ACTIVE — sticky soft-pause, no more nudges.
                    meta["execution_stage"] = "DELIVERY_SOFT_PAUSE"
                    meta["ops_soft_pause"] = {
                        "at": now.isoformat(),
                        "reason": "stale_progress_exhausted",
                        "stale_minutes": stale_minutes,
                        "nudge_count": nudge_count,
                    }
                    meta["stale_progress_soft_noted_at"] = now.isoformat()
                    meta["live_action"] = build_live_action(
                        "执行暂无进展，已暂停空转。可点「继续此目标」或在对话补充方向。",
                        stage="DELIVERY_SOFT_PAUSE",
                        event_type="STALE_PROGRESS_SOFT_PAUSE",
                        detail=f"stale_for_minutes={stale_minutes}",
                    )
                    meta["awaiting_human_intervention"] = False
                    meta.pop("pending_delivery_gap_human", None)
                    meta.pop("stale_progress_handoff_at", None)
                    goal.metadata_json = meta
                    flag_modified(goal, "metadata_json")
                    if goal.app_project_id is not None:
                        await _append_soft_note(
                            session,
                            project_id=goal.app_project_id,
                            goal_id=goal.id,
                            content=(
                                "长时间无进展，已停止自动空转。"
                                "可在对话补充方向或点「继续此目标」；无需点「总是允许」。"
                            ),
                            stale_minutes=stale_minutes,
                        )
                    stats["soft_noted"] += 1
                    continue

                if goal.app_project_id is None:
                    continue

                meta["stale_progress_nudge_count"] = nudge_count + 1
                meta["stale_progress_nudged_at"] = now.isoformat()
                meta["awaiting_human_intervention"] = False
                meta.pop("pending_delivery_gap_human", None)
                meta.pop("stale_progress_handoff_at", None)
                meta["execution_stage"] = str(meta.get("execution_stage") or "ACTIVE")
                meta["live_action"] = build_live_action(
                    "进展停滞，已自动继续推进（无需确认）",
                    stage=str(meta.get("execution_stage") or "ACTIVE"),
                    event_type="STALE_PROGRESS_AUTO_CONTINUE",
                    detail=f"stale_for_minutes={stale_minutes};nudge={nudge_count + 1}",
                )
                goal.metadata_json = meta
                flag_modified(goal, "metadata_json")

                resume_key = make_idempotency_key(
                    "stale-progress-nudge",
                    goal.id,
                    f"{now.isoformat()}:{uuid.uuid4().hex[:8]}",
                )
                session.add(
                    make_outbox_event(
                        EventEnvelope(
                            event_type=GOAL_EXECUTION_REQUESTED,
                            aggregate_type="goal",
                            aggregate_id=goal.id,
                            aggregate_version=goal.version,
                            payload={
                                "goal_id": str(goal.id),
                                "app_project_id": str(goal.app_project_id),
                                "actor": "regent-core:stale-progress",
                                "idempotency_key": resume_key,
                                "reason": "stale_progress_auto_continue",
                            },
                            idempotency_key=resume_key,
                            correlation_id=goal.correlation_id,
                        )
                    )
                )
                stats["auto_continued"] += 1
                logger.info(
                    "stale progress auto-continue",
                    extra={
                        "goal_id": str(goal.id),
                        "age_min": stale_minutes,
                        "nudge": nudge_count + 1,
                    },
                )
                continue

            # 5–12 min: warn via live_action without creating a task.
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


# Ship-first: in-process reclaim of GENERATING zombies (was ops-only script).
_ZOMBIE_RUN_HOURS = 2
_ZOMBIE_GOAL_HOURS = 2
_ZOMBIE_LOCK_KEY = 87201402


async def reclaim_generating_zombies(
    sessions: async_sessionmaker[AsyncSession],
    *,
    stale_run_hours: float = _ZOMBIE_RUN_HOURS,
    stale_goal_hours: float = _ZOMBIE_GOAL_HOURS,
    limit: int = 80,
) -> dict[str, int]:
    """Fail stale GENERATING runs and ACTIVE goals with no live outbox/run.

    Replaces reliance on ops/reclaim_generating_zombies.py for the common case.
    """
    from regent.infrastructure.models import (
        GenerationPlanModel,
        GenerationRunModel,
        OutboxEventModel,
        RequirementRevisionModel,
    )

    stats = {
        "stale_runs_failed": 0,
        "zombie_goals_failed": 0,
        "requeued": 0,
        "skipped_lock": 0,
    }
    now = datetime.now(UTC)
    run_cutoff = now - timedelta(hours=stale_run_hours)
    goal_cutoff = now - timedelta(hours=stale_goal_hours)

    async with sessions() as session, session.begin():
        locked = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"),
            {"k": _ZOMBIE_LOCK_KEY},
        )
        if not locked:
            stats["skipped_lock"] = 1
            return stats

        stale_runs = list(
            (
                await session.execute(
                    select(GenerationRunModel, GoalModel)
                    .join(
                        GenerationPlanModel,
                        GenerationPlanModel.id == GenerationRunModel.plan_id,
                    )
                    .join(
                        RequirementRevisionModel,
                        RequirementRevisionModel.id
                        == GenerationPlanModel.requirement_revision_id,
                    )
                    .join(GoalModel, GoalModel.id == RequirementRevisionModel.goal_id)
                    .where(
                        GenerationRunModel.status == "GENERATING",
                        GenerationRunModel.updated_at < run_cutoff,
                    )
                    .order_by(GenerationRunModel.updated_at.asc())
                    .limit(limit)
                )
            ).all()
        )
        for run, goal in stale_runs:
            run.status = "FAILED"
            run.failure_code = "ZOMBIE_STALE_GENERATING"
            stats["stale_runs_failed"] += 1
            meta = dict(goal.metadata_json or {})
            req_rev = meta.get("requirement_revision_id")
            cap_plan = meta.get("capability_resolution_plan_id")
            if (
                goal.status == "ACTIVE"
                and req_rev
                and cap_plan
                and goal.app_project_id is not None
            ):
                resume_key = make_idempotency_key(
                    "zombie-reclaim",
                    goal.id,
                    now.strftime("%Y%m%d%H"),
                )
                existing_rows = list(
                    (
                        await session.execute(
                            select(OutboxEventModel.payload).where(
                                OutboxEventModel.event_type.in_(
                                    (
                                        GOAL_EXECUTION_REQUESTED,
                                        "GenerationRunRequested",
                                    )
                                ),
                                OutboxEventModel.aggregate_id == goal.id,
                                OutboxEventModel.status.in_(
                                    ("PENDING", "DISPATCHING", "DISPATCHED")
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                already = any(
                    isinstance(p, dict) and str(p.get("idempotency_key") or "") == resume_key
                    for p in existing_rows
                )
                if not already:
                    session.add(
                        make_outbox_event(
                            EventEnvelope(
                                event_type=GOAL_EXECUTION_REQUESTED,
                                aggregate_type="goal",
                                aggregate_id=goal.id,
                                aggregate_version=goal.version,
                                payload={
                                    "goal_id": str(goal.id),
                                    "app_project_id": str(goal.app_project_id),
                                    "requirement_revision_id": str(req_rev),
                                    "capability_resolution_plan_id": str(cap_plan),
                                    "actor": "regent-core:zombie-reclaim",
                                    "idempotency_key": resume_key,
                                    "reason": "zombie_stale_generating",
                                },
                                idempotency_key=resume_key,
                                correlation_id=goal.correlation_id,
                            )
                        )
                    )
                    stats["requeued"] += 1

        zombie_goals = list(
            (
                await session.execute(
                    select(GoalModel)
                    .where(
                        GoalModel.status == "ACTIVE",
                        GoalModel.updated_at < goal_cutoff,
                    )
                    .order_by(GoalModel.updated_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for goal in zombie_goals:
            meta = dict(goal.metadata_json or {})
            if str(meta.get("execution_stage") or "") != "GENERATING":
                continue
            if meta.get("ops_soft_pause") or str(meta.get("execution_stage") or "") == (
                "DELIVERY_SOFT_PAUSE"
            ):
                continue
            has_run = await session.scalar(
                select(GenerationRunModel.id)
                .join(
                    GenerationPlanModel,
                    GenerationPlanModel.id == GenerationRunModel.plan_id,
                )
                .join(
                    RequirementRevisionModel,
                    RequirementRevisionModel.id
                    == GenerationPlanModel.requirement_revision_id,
                )
                .where(
                    RequirementRevisionModel.goal_id == goal.id,
                    GenerationRunModel.status == "GENERATING",
                )
                .limit(1)
            )
            if has_run is not None:
                continue
            has_outbox = await session.scalar(
                select(OutboxEventModel.id).where(
                    OutboxEventModel.aggregate_id == goal.id,
                    OutboxEventModel.event_type == "GenerationRunRequested",
                    OutboxEventModel.status.in_(("PENDING", "DISPATCHING")),
                )
            )
            if has_outbox is not None:
                continue
            # Soft-pause rather than FAILED: user can CONTINUE / CORRECT without
            # recreating the Goal (Goal evolution path).
            meta["execution_stage"] = "DELIVERY_SOFT_PAUSE"
            meta["ops_soft_pause"] = {
                "at": now.isoformat(),
                "reason": "zombie_generating_no_run",
            }
            meta["live_action"] = build_live_action(
                "执行已停滞并暂停：可修正目标后继续，或停止",
                stage="DELIVERY_SOFT_PAUSE",
                event_type="ZOMBIE_SOFT_PAUSE",
            )
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")
            stats["zombie_goals_failed"] += 1

    if stats["stale_runs_failed"] or stats["zombie_goals_failed"]:
        logger.info("reclaim generating zombies", extra=stats)
    return stats
