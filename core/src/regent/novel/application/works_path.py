"""Critical path services."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application import memory as memory_app
from regent.novel.application.events import append_event
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_constants import (
    CHAPTERS_PER_NODE,
    MAX_PATH_NODES,
    MIN_PATH_NODES,
)
from regent.novel.application.works_input import bump_input_version
from regent.novel.domain.errors import Conflict, ValidationFailed
from regent.novel.domain.models import (
    CriticalNode,
    CriticalPathOut,
    CriticalPathUpdate,
    PathChangeImpact,
    PathNodeType,
)
from regent.novel.domain.states import (
    ChapterRunState,
    StoryWorkState,
    assert_story_work_transition,
)
from regent.novel.infrastructure.models import ChapterRunModel, CriticalNodeModel, CriticalPathModel


def _path_out(path: CriticalPathModel, nodes: list[CriticalNode]) -> CriticalPathOut:
    return CriticalPathOut(
        nodes=nodes,
        dependency_edges=[dict(e) for e in (path.dependency_edges or [])],
        frozen_through_chapter=int(path.frozen_through_chapter),
        version=int(path.version),
    )


async def get_critical_path(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> CriticalPathOut:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work_id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return CriticalPathOut()
    node_rows = await session.scalars(
        select(CriticalNodeModel)
        .where(CriticalNodeModel.path_id == path.id)
        .order_by(CriticalNodeModel.ordinal)
    )
    nodes = [
        CriticalNode(
            node_id=n.node_id,
            ordinal=n.ordinal,
            title=n.title,
            node_type=PathNodeType(n.node_type),
            promise=n.promise,
            preconditions=list(n.preconditions or []),
            consequences=list(n.consequences or []),
            requires_human=bool(n.requires_human),
            locked=bool(n.locked),
        )
        for n in node_rows.all()
    ]
    return _path_out(path, nodes)


def _node_changes(
    *,
    current_nodes: list[CriticalNode],
    next_nodes: list[CriticalNode],
) -> list[tuple[int, str]]:
    """路径改动清单：``(起作用的 ordinal, 涉及的主题)``。

    改动判定只有这一个口径——受影响章节与记忆失效范围都从它推导。若两处各判
    一次，就会出现「算得出受影响章节、却失效不了对应记忆」（或反之）的静默缺口。

    判定依据：**改路径改的是承诺**。标题、顺序、节点类型、承诺内容任一项变了，
    依托它的记忆就不再可信；被删掉的节点同样算改动。
    """
    current_by_id = {n.node_id: n for n in current_nodes}
    next_by_id = {n.node_id: n for n in next_nodes}
    changes: list[tuple[int, str]] = []
    for node in next_nodes:
        prev = current_by_id.get(node.node_id)
        if prev is None:
            changes.append((int(node.ordinal), node.title))
            continue
        if (
            prev.title != node.title
            or int(prev.ordinal) != int(node.ordinal)
            or prev.promise != node.promise
            or prev.node_type != node.node_type
        ):
            changes.append((int(node.ordinal), prev.title))
            changes.append((int(node.ordinal), node.title))
    for node in current_nodes:
        if node.node_id not in next_by_id:
            changes.append((int(node.ordinal), node.title))
    seen: set[tuple[int, str]] = set()
    result: list[tuple[int, str]] = []
    for ordinal, title in changes:
        title = str(title).strip()
        if not title or (ordinal, title) in seen:
            continue
        seen.add((ordinal, title))
        result.append((ordinal, title))
    return result


def _preview_impact(
    *,
    current_nodes: list[CriticalNode],
    next_nodes: list[CriticalNode],
    frozen_through_chapter: int,
    latest_chapter_no: int,
) -> PathChangeImpact:
    """影响范围：按 ordinal 定位改动起点，估算受影响章节。不含虚假精确百分比。"""
    changes = _node_changes(current_nodes=current_nodes, next_nodes=next_nodes)
    if not changes:
        return PathChangeImpact(
            affected_chapters=[],
            frozen_conflict=False,
            frozen_through_chapter=frozen_through_chapter,
        )

    first_ordinal = min(ordinal for ordinal, _title in changes)
    # 一个节点约覆盖 CHAPTERS_PER_NODE 章
    start_chapter = max(1, (first_ordinal - 1) * CHAPTERS_PER_NODE + 1)
    end_chapter = max(start_chapter, latest_chapter_no)
    affected = list(range(start_chapter, end_chapter + 1))

    return PathChangeImpact(
        affected_chapters=affected,
        frozen_conflict=start_chapter <= frozen_through_chapter,
        frozen_through_chapter=frozen_through_chapter,
        eta_minutes_min=len(affected) * 2,
        eta_minutes_max=len(affected) * 6,
        cost_ceiling_minor=len(affected) * 8500,  # 影子价格：¥85/章上限
    )


async def update_critical_path(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    payload: CriticalPathUpdate,
) -> tuple[CriticalPathOut, PathChangeImpact]:
    """FR-05：expected_version 冲突保护 + 影响预览。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)

    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work_id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    current_version = int(path.version) if path else 0
    if payload.expected_version != current_version:
        raise Conflict(
            "critical path version conflict",
            current_version=current_version,
            conflict_summary={
                "expected_version": payload.expected_version,
                "current_version": current_version,
                "reason": "path_updated_elsewhere",
            },
        )

    if not MIN_PATH_NODES <= len(payload.nodes) <= MAX_PATH_NODES:
        raise ValidationFailed(
            f"critical path must contain {MIN_PATH_NODES}-{MAX_PATH_NODES} nodes"
        )
    ordinals = [n.ordinal for n in payload.nodes]
    if len(set(ordinals)) != len(ordinals):
        raise ValidationFailed("critical node ordinals must be unique")

    current_nodes = (await get_critical_path(session, owner_id=owner_id, work_id=work_id)).nodes

    frozen_through = int(path.frozen_through_chapter) if path else 0
    impact = _preview_impact(
        current_nodes=current_nodes,
        next_nodes=payload.nodes,
        frozen_through_chapter=frozen_through,
        latest_chapter_no=int(work.latest_chapter_no),
    )
    if impact.frozen_conflict:
        raise Conflict(
            "修改落在已固化章节内，不能改动",
            current_version=current_version,
            conflict_summary={
                "frozen_through_chapter": frozen_through,
                "first_affected_chapter": impact.affected_chapters[0]
                if impact.affected_chapters
                else None,
                "reason": "chapter_already_frozen",
            },
        )

    new_path = CriticalPathModel(
        id=uuid.uuid4(),
        work_id=work_id,
        version=current_version + 1,
        frozen_through_chapter=frozen_through,
        node_count=len(payload.nodes),
        dependency_edges=[dict(e) for e in payload.dependency_edges],
    )
    session.add(new_path)
    await session.flush()
    for node in payload.nodes:
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(),
                path_id=new_path.id,
                node_id=node.node_id,
                ordinal=node.ordinal,
                title=node.title,
                node_type=node.node_type.value,
                promise=node.promise,
                preconditions=list(node.preconditions),
                consequences=list(node.consequences),
                requires_human=node.requires_human,
                locked=node.locked,
            )
        )
    await session.flush()

    work.version += 1
    if work.state in (StoryWorkState.READY.value, StoryWorkState.RUNNING.value):
        assert_story_work_transition(work.state, StoryWorkState.RECOMPUTING.value)
        work.state = StoryWorkState.RECOMPUTING.value
    await session.flush()

    # 路径变更：在途章节的输入版本递增，旧方向产出作废（P0-4）
    _IN_FLIGHT = (
        ChapterRunState.QUEUED.value,
        ChapterRunState.RUNNING.value,
        ChapterRunState.RETRYABLE_FAILED.value,
        ChapterRunState.AWAITING_INPUT.value,
        ChapterRunState.PENDING_DECISION.value,
    )
    affected_runs = (
        await session.scalars(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id,
                ChapterRunModel.branch_id == work.branch_id,
                ChapterRunModel.state.in_(_IN_FLIGHT),
            )
        )
    ).all()
    for run in affected_runs:
        await bump_input_version(session, run=run, reason="critical_path_changed")

    # R3：路径变更使「依托旧路径」的记忆失效。依赖图完整时只失效被改动主题的下游
    # 子图；图不完整时保守失效同类整批——世界规则与已发生的事实不因改路径而消失。
    # 失效是打标记，不删不改，事实链不动。
    changed_subjects = sorted(
        {
            title
            for _ordinal, title in _node_changes(
                current_nodes=current_nodes, next_nodes=payload.nodes
            )
        }
    )
    invalidated, conservative = await memory_app.invalidate_changed(
        session,
        work=work,
        changed_subjects=changed_subjects,
        reason="critical_path_changed",
        fallback_kinds=("promise", "character_arc"),
    )

    await append_event(
        session,
        work_id=work_id,
        event_type="critical_path.updated",
        data={
            "version": new_path.version,
            "node_count": len(payload.nodes),
            "affected_chapters": impact.affected_chapters,
            "change_note": payload.change_note,
            "invalidated_memory_items": invalidated,
            "changed_subjects": changed_subjects,
            # 记下失效走到哪条路径：承诺"只失效子图"却整批失效，是必须能查出来的事
            "invalidated_scope": "conservative_batch" if conservative else "dependency_subgraph",
        },
        branch_id=work.branch_id,
    )
    return _path_out(new_path, payload.nodes), impact


path_out = _path_out
node_changes = _node_changes
preview_impact = _preview_impact


# ---------------------------------------------------------------------------
# 卷扩展（30 万字架构）
# ---------------------------------------------------------------------------
