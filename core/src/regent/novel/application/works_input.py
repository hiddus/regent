"""input_version 递增：用户改意 / 裁决落定后使旧调用键失效。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.infrastructure.models import ChapterRunModel


async def bump_input_version(
    session: AsyncSession,
    *,
    run: ChapterRunModel,
    reason: str,
) -> int:
    """用户改意/路径变更：递增 input_version，使旧方向的调用与产物失效（P0-4）。"""
    run.input_version = int(run.input_version or 1) + 1
    run.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=run.work_id,
        event_type="chapter.input_version_bumped",
        data={
            "chapter_no": run.chapter_no,
            "reason": reason,
            "input_version": run.input_version,
        },
        branch_id=run.branch_id,
        chapter_no=run.chapter_no,
    )
    return int(run.input_version)
