"""把审定后的故事主线写入关键路径，供编剧按节点写剧本。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from regent.novel.application.works_constants import CHAPTERS_PER_NODE
from regent.novel.domain.models import PathNodeType
from regent.novel.domain.story_mainline import node_payload
from regent.novel.infrastructure.models import (
    CriticalNodeModel,
    CriticalPathModel,
    StoryWorkModel,
    VolumeModel,
)

_REVIEWED_KEY = "mainline_reviewed_node_count"


def reviewed_node_count(work: StoryWorkModel) -> int:
    bible = getattr(work, "story_bible", None)
    if not isinstance(bible, dict):
        return 0
    try:
        return int(bible.get(_REVIEWED_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def mark_mainline_reviewed(work: StoryWorkModel, node_count: int) -> None:
    bible = dict(getattr(work, "story_bible", None) or {})
    bible[_REVIEWED_KEY] = int(node_count)
    work.story_bible = bible
    try:
        flag_modified(work, "story_bible")
    except (AttributeError, TypeError):
        pass


async def load_mainline(
    session: AsyncSession,
    work_id: uuid.UUID,
) -> tuple[CriticalPathModel | None, list[CriticalNodeModel]]:
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work_id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return None, []
    nodes = list(
        (
            await session.scalars(
                select(CriticalNodeModel)
                .where(CriticalNodeModel.path_id == path.id)
                .order_by(CriticalNodeModel.ordinal)
            )
        ).all()
    )
    return path, nodes


def _node_type(raw: str) -> str:
    try:
        return PathNodeType(str(raw or "").upper()).value
    except ValueError:
        return PathNodeType.CUSTOM.value


def _add_node(
    path_id: uuid.UUID,
    *,
    ordinal: int,
    title: str,
    node_type: str,
    promise: str,
    preconditions: list[str],
    consequences: list[str],
) -> CriticalNodeModel:
    return CriticalNodeModel(
        id=uuid.uuid4(),
        path_id=path_id,
        node_id=f"n{ordinal:02d}",
        ordinal=ordinal,
        title=title[:200],
        node_type=_node_type(node_type),
        promise=promise,
        preconditions=list(preconditions or []),
        consequences=list(consequences or []),
        requires_human=False,
        locked=False,
        volume_no=1,
        arc_no=(ordinal - 1) // CHAPTERS_PER_NODE + 1,
    )


async def _extend_volume(session: AsyncSession, work_id: uuid.UUID, node_count: int) -> None:
    volume = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work_id,
            VolumeModel.volume_no == 1,
        )
    )
    if volume is None:
        return
    end = node_count * CHAPTERS_PER_NODE
    if int(volume.end_chapter_no or 0) < end:
        volume.end_chapter_no = end


async def replace_mainline_from(
    session: AsyncSession,
    work: StoryWorkModel,
    path: CriticalPathModel | None,
    nodes: list[CriticalNodeModel],
    outline_nodes: list[Any],
    *,
    keep: int,
) -> tuple[CriticalPathModel, list[CriticalNodeModel]]:
    """保留前 keep 个已走节点，其后换成新大纲。"""
    if path is None:
        path = CriticalPathModel(
            id=uuid.uuid4(),
            work_id=work.id,
            version=1,
            frozen_through_chapter=0,
            node_count=0,
            dependency_edges=[],
        )
        session.add(path)
        await session.flush()
        nodes = []
    kept = [node for node in nodes if int(node.ordinal) <= keep]
    dropped = [node for node in nodes if int(node.ordinal) > keep]
    for node in dropped:
        await session.delete(node)
    # 同一 flush 里先插后删会撞上 (path_id, ordinal) 唯一约束。
    if dropped:
        await session.flush()
    start = keep + 1
    created = list(kept)
    for offset, item in enumerate(outline_nodes):
        ordinal = start + offset
        created.append(
            _add_node(
                path.id,
                ordinal=ordinal,
                title=str(getattr(item, "title", "") or ""),
                node_type=str(getattr(item, "node_type", "") or ""),
                promise=str(getattr(item, "promise", "") or ""),
                preconditions=list(getattr(item, "preconditions", None) or []),
                consequences=list(getattr(item, "consequences", None) or []),
            )
        )
        session.add(created[-1])
    path.node_count = len(created)
    await _extend_volume(session, work.id, len(created))
    await session.flush()
    return path, created


def outline_prompt_nodes(nodes: list[CriticalNodeModel]) -> list[dict[str, Any]]:
    return [node_payload(node) for node in nodes]
