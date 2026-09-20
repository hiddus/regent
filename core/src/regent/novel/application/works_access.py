"""作品访问与所有权查询（从 works 抽出，打破循环依赖的第一刀）。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.domain.errors import NotFound
from regent.novel.infrastructure.models import StoryWorkModel


async def get_owned_work(
    session: AsyncSession, *, work_id: uuid.UUID, owner_id: uuid.UUID
) -> StoryWorkModel:
    """G-12：无 owner 条件的私有读取 fail closed；已删除作品视为不存在。"""
    row = await session.scalar(
        select(StoryWorkModel).where(
            StoryWorkModel.id == work_id,
            StoryWorkModel.owner_id == owner_id,
            StoryWorkModel.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFound("work not found")
    return row
