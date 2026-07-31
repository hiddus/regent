"""Publish Core's current action for console live sync (not conversation spam)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.infrastructure.models import GoalModel

# Human-readable defaults for common EVENT types (milestone-level).
EVENT_LIVE_SUMMARY: dict[str, str] = {
    "GOAL_UNDERSTANDING_READY": "正在理解你的产品想法",
    "GOAL_EXECUTION_QUEUED": "已开始执行目标",
    "DISCOVERY_ROUND_CREATED": "正在进行市场调研",
    "DISCOVERY_ROUND_REQUESTED": "正在继续市场调研",
    "DISCOVERY_COMPLETED": "市场调研刚完成，正在进入下一步",
    "RESEARCH_MORE_ADAPT_CONTINUE": "正在深入取证调研",
    "REQUIREMENT_REQUESTED": "正在规划产品方案",
    "REQUIREMENT_VALIDATED": "产品方案已规划完成",
    "CAPABILITY_RESOLUTION_REQUESTED": "正在准备技术方案",
    "CAPABILITY_RESOLUTION_PLANNED": "技术方案已就绪",
    "GENERATION_RUN_REQUESTED": "正在生成应用代码",
    "WORKSPACE_SNAPSHOT_READY": "应用源码已打包",
    "DEPENDENCY_RESOLUTION_REQUESTED": "正在解析依赖",
    "APP_BUILD_REQUESTED": "正在构建并做质量检查",
    "APP_BUILD_PASSED": "构建已通过，准备预览",
    "PREVIEW_DEPLOYMENT_REQUESTED": "正在部署预览环境",
    "PREVIEW_DEPLOYMENT_SUCCEEDED": "预览已部署",
    "PREVIEW_READY": "预览已就绪",
    "ATTAINMENT_RECOVERY_STARTED": "目标未达成，正在重新规划",
    "DELIVERY_GAP_CAPABILITY_ESCALATED": "正在升级能力并重试交付",
    "MILESTONE_ATTAINED": "当前阶段已达成",
    "GOAL_ACHIEVED": "目标已完成",
    "HUMAN_TASK_REQUIRED": "等待你确认以继续",
    "DELIVERY_GAP_EXHAUSTED": "等待你确认以继续",
    "DELIVERY_GAP_HUMAN_APPROVED": "已批准，正在重新规划并继续生成",
}

_MAX_TOOL_EVENTS = 20


def build_live_action(
    summary: str,
    *,
    stage: str | None = None,
    detail: str | None = None,
    turn: int | None = None,
    event_type: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary": summary[:240],
        "stage": stage,
        "detail": (detail[:400] if detail else None),
        "turn": turn,
        "event_type": event_type,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if tool:
        payload["tool"] = tool[:128]
    return payload


def summary_for_event(message_type: str, content: str | None = None) -> str:
    base = EVENT_LIVE_SUMMARY.get(message_type)
    if base:
        return base
    if content:
        one = " ".join(content.split())
        return one[:120] if one else f"正在执行：{message_type}"
    return f"正在执行：{message_type}"


def _append_tool_event(meta: dict[str, Any], tool_event: dict[str, Any], tool: str | None) -> None:
    events = meta.get("tool_events")
    if not isinstance(events, list):
        events = []
    events.append(dict(tool_event))
    meta["tool_events"] = events[-_MAX_TOOL_EVENTS:]


def merge_live_action_into_metadata(
    metadata: dict[str, Any] | None,
    summary: str,
    *,
    stage: str | None = None,
    detail: str | None = None,
    turn: int | None = None,
    event_type: str | None = None,
    tool: str | None = None,
    tool_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    resolved_tool = tool or (str(tool_event.get("tool")) if tool_event and tool_event.get("tool") else None)
    if tool_event is not None:
        _append_tool_event(meta, tool_event, resolved_tool)
    meta["live_action"] = build_live_action(
        summary,
        stage=stage or (str(meta.get("execution_stage")) if meta.get("execution_stage") else None),
        detail=detail,
        turn=turn,
        event_type=event_type,
        tool=resolved_tool,
    )
    return meta


async def set_goal_live_action(
    sessions: async_sessionmaker[AsyncSession],
    goal_id: uuid.UUID,
    summary: str,
    *,
    stage: str | None = None,
    detail: str | None = None,
    turn: int | None = None,
    event_type: str | None = None,
    tool: str | None = None,
    tool_event: dict[str, Any] | None = None,
) -> None:
    """Short transaction: update only live_action for console SSE/status."""
    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return
        goal.metadata_json = merge_live_action_into_metadata(
            goal.metadata_json if isinstance(goal.metadata_json, dict) else {},
            summary,
            stage=stage,
            detail=detail,
            turn=turn,
            event_type=event_type,
            tool=tool,
            tool_event=tool_event,
        )
        flag_modified(goal, "metadata_json")


def apply_live_action_on_goal(
    goal: GoalModel,
    summary: str,
    *,
    stage: str | None = None,
    detail: str | None = None,
    turn: int | None = None,
    event_type: str | None = None,
    tool: str | None = None,
    tool_event: dict[str, Any] | None = None,
) -> None:
    """In-session update (caller owns the transaction)."""
    goal.metadata_json = merge_live_action_into_metadata(
        goal.metadata_json if isinstance(goal.metadata_json, dict) else {},
        summary,
        stage=stage,
        detail=detail,
        turn=turn,
        event_type=event_type,
        tool=tool,
        tool_event=tool_event,
    )
    flag_modified(goal, "metadata_json")
