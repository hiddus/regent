"""Project lifecycle actions emitted after a goal reaches attainment."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from regent.application.conversation_service import append_project_timeline_event
from regent.infrastructure.models import GoalModel


async def suggest_project_next_steps(
    session: AsyncSession,
    project_id: uuid.UUID,
    goal_id: uuid.UUID,
) -> None:
    goal = await session.get(GoalModel, goal_id)
    if goal is None or not dict(goal.metadata_json or {}).get("auto_maintain"):
        return
    await append_project_timeline_event(
        session,
        project_id,
        "PROJECT_LIFECYCLE_SUGGESTION",
        (
            "当前阶段目标已完成。项目已启用自动维护模式。"
            "请选择下一步方向：\n"
            "1. 创建维护目标（持续监控和优化）\n"
            "2. 启动新功能阶段\n"
            "3. 归档项目"
        ),
        {
            "goal_id": str(goal_id),
            "project_id": str(project_id),
            "suggested_actions": [
                "create_maintenance_goal",
                "start_new_phase",
                "archive_project",
            ],
        },
    )
