"""长期记忆的落库、召回与失效（Plan §4 R3）。

分寸（与本项目其它处一致）：

- **抽取不经过模型**：Canon 事实进、记忆条目出，全部由 ``domain.memory`` 的
  确定性规则决定，因此「这条规则为什么被记住」可以举证。
- **召回按需且有下限**：上下文预算有限，但**未兑现的承诺永不因限额被裁掉**。
- **改意只失效不修改**：``invalidate`` 打 ``invalidated_at``，事实链不动。
- **重演取保守章窗口**：改某章后重生成该章起连续后续范围；不再宣称最小依赖子图。
  逻辑槽对外为 fact / known / promise / summary（存储仍用细 kind）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.domain import memory as domain
from regent.novel.infrastructure.models import (
    MemoryEdgeModel,
    MemoryItemModel,
    StoryWorkModel,
)

DEFAULT_RECALL_LIMIT = 24
INDEPENDENT_MARKER = domain.INDEPENDENT_MARKER


def _to_item(row: MemoryItemModel) -> domain.MemoryItem:
    return domain.MemoryItem(
        key=row.item_key,
        kind=row.kind,
        subject=row.subject,
        content=row.content,
        entities=tuple(row.entities or ()),
        state=row.state,
        confidence=row.confidence,
        source_chapter_no=int(row.source_chapter_no or 0),
        source_hash=row.source_hash or "",
        invalidated=row.invalidated_at is not None,
        basis=str(row.basis or ""),
        resolved_chapter_no=int(row.resolved_chapter_no or 0),
    )


async def _rows(session: AsyncSession, work: StoryWorkModel) -> list[MemoryItemModel]:
    return list(
        (
            await session.scalars(
                select(MemoryItemModel).where(
                    MemoryItemModel.work_id == work.id,
                    MemoryItemModel.branch_id == work.branch_id,
                )
            )
        ).all()
    )


async def cast_of(session: AsyncSession, work: StoryWorkModel) -> list[str]:
    """在册人物名。正式事实不带分类标记时，它是唯一可信的结构信号。"""
    from regent.novel.infrastructure.models import PersonaSpecModel

    return sorted(
        {
            str(name).strip()
            for name in (
                await session.scalars(
                    select(PersonaSpecModel.name).where(
                        PersonaSpecModel.work_id == work.id
                    )
                )
            ).all()
            if str(name).strip()
        }
    )


async def _link_dependencies(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    previous: dict[str, domain.MemoryItem],
    written: Sequence[domain.MemoryItem],
) -> int:
    """依赖边自动建图已退役（职责删减）。

    纠错与改意一律走保守章窗口重做，不再宣称最小子图。函数保留签名以免
    ``record_chapter_memory`` 调用方立刻炸；恒返回 0。手工 ``link_memory``
    仍可供离线对照使用。
    """
    del session, work, previous, written
    return 0


def _keep_resolution(
    previous: domain.MemoryItem | None, item: domain.MemoryItem
) -> domain.MemoryItem:
    """已兑现的承诺不得被后来的同内容事实重新打开（B-01）。

    同一句承诺在第 9 章又写了一次，抽取器不知道它已经兑现过，会按默认状态
    重新记成 OPEN——伏笔于是「还完了又欠上」，且这条错误会一直挂到下次兑现。
    已兑现是既成事实，只能由兑现/废弃改变，不能被重述改变。
    """
    if previous is None or previous.state != "RESOLVED" or item.state != "OPEN":
        return item
    return replace(
        item,
        state="RESOLVED",
        resolved_chapter_no=previous.resolved_chapter_no,
        source_chapter_no=previous.source_chapter_no,
    )


async def record_chapter_memory(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    chapter_no: int,
    facts: Sequence[dict[str, Any]],
    source_hash: str = "",
    cast: Sequence[str] | None = None,
) -> list[domain.MemoryItem]:
    """把一章的 Canon 事实抽取成长期记忆并落库（同键幂等，后写覆盖）。"""
    existing = {row.item_key: row for row in await _rows(session, work)}
    merged: dict[str, domain.MemoryItem] = {
        row.item_key: _to_item(row) for row in existing.values()
    }
    names = list(cast) if cast is not None else await cast_of(session, work)
    before = dict(merged)
    for item in domain.extract_items(
        list(facts), chapter_no=chapter_no, source_hash=source_hash, cast=names
    ):
        merged[item.key] = _keep_resolution(merged.get(item.key), item)
    # 兑现：本章事实里出现 resolves/payoff 的旧承诺标记为 RESOLVED
    extracted = domain.resolve_items(merged.values(), list(facts), chapter_no=chapter_no)
    written: list[domain.MemoryItem] = []
    for item in extracted:
        row = existing.get(item.key)
        if row is None:
            row = MemoryItemModel(
                id=uuid.uuid4(),
                work_id=work.id,
                branch_id=work.branch_id,
                item_key=item.key,
                kind=item.kind,
                subject=item.subject,
                content=item.content,
                entities=list(item.entities),
                state=item.state,
                confidence=item.confidence,
                source_chapter_no=item.source_chapter_no,
                source_hash=item.source_hash,
                basis=item.basis,
                resolved_chapter_no=item.resolved_chapter_no,
                memory_version=1,
            )
            session.add(row)
        else:
            row.content = item.content
            row.entities = list(item.entities)
            row.state = item.state
            row.confidence = item.confidence
            row.source_chapter_no = item.source_chapter_no
            row.source_hash = item.source_hash
            row.basis = item.basis
            row.resolved_chapter_no = item.resolved_chapter_no
            row.memory_version = int(row.memory_version or 1) + 1
        written.append(item)
    await session.flush()
    await _link_dependencies(session, work=work, previous=before, written=written)
    return written


async def recall_memory(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    chapter_no: int,
    kinds: Sequence[str] = (),
    entities: Sequence[str] = (),
    limit: int = DEFAULT_RECALL_LIMIT,
) -> domain.MemoryBundle:
    """按需召回。返回结果与来源摘要，便于上下文 manifest 留痕（G-02）。"""
    rows = await _rows(session, work)
    items = [_to_item(row) for row in rows]
    picked = domain.recall(
        items, chapter_no=chapter_no, kinds=kinds, entities=entities, limit=limit
    )
    return domain.MemoryBundle(
        items=tuple(picked),
        limit=limit,
        chapter_no=chapter_no,
        requested_kinds=tuple(kinds),
        requested_entities=tuple(entities),
    )


async def invalidate_memory(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    reason: str,
    kinds: Sequence[str] = (),
) -> int:
    """用户改意：把条目置失效（保留不删），使后续召回不再带旧方向的记忆。

    只失效当前分支；历史条目留痕，用于说明「为什么这一版没有沿用上一版设定」。
    """
    query = select(MemoryItemModel).where(
        MemoryItemModel.work_id == work.id,
        MemoryItemModel.branch_id == work.branch_id,
        MemoryItemModel.invalidated_at.is_(None),
    )
    if kinds:
        query = query.where(MemoryItemModel.kind.in_(list(kinds)))
    rows = list((await session.scalars(query)).all())
    now = datetime.now(UTC)
    for row in rows:
        row.invalidated_at = now
        row.invalidated_reason = (reason or "direction_changed")[:200]
    await session.flush()
    return len(rows)


async def link_memory(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    upstream_key: str,
    downstream_key: str,
    edge_kind: str = "depends",
) -> None:
    """登记依赖边（幂等）。

    ``independent`` 边用 ``INDEPENDENT_MARKER`` 作上游，表示「这一条已确认没有
    上游」。没有这种边时，孤立节点的独立性无从证明。
    """
    existing = await session.scalar(
        select(MemoryEdgeModel).where(
            MemoryEdgeModel.work_id == work.id,
            MemoryEdgeModel.branch_id == work.branch_id,
            MemoryEdgeModel.upstream_key == upstream_key,
            MemoryEdgeModel.downstream_key == downstream_key,
        )
    )
    if existing is not None:
        return
    session.add(
        MemoryEdgeModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            upstream_key=upstream_key,
            downstream_key=downstream_key,
            edge_kind=edge_kind,
        )
    )
    await session.flush()


async def plan_replay(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    changed_subjects: Sequence[str],
) -> domain.ReplayPlan:
    """计划重演范围：依赖完整时给最小子图，不完整时返回 ``complete=False``。

    一条依赖边都没有，说明图还没建立——这时"没有下游"和"下游未知"无法区分，
    必须按未知处理，由调用方保守重做，而不是据此认为只需要重演一个节点。
    """
    rows = await _rows(session, work)
    items = [_to_item(row) for row in rows]
    records = list(
        (
            await session.execute(
                select(
                    MemoryEdgeModel.upstream_key,
                    MemoryEdgeModel.downstream_key,
                    MemoryEdgeModel.edge_kind,
                ).where(
                    MemoryEdgeModel.work_id == work.id,
                    MemoryEdgeModel.branch_id == work.branch_id,
                )
            )
        ).all()
    )
    edges = [(up, down) for up, down, kind in records if kind != "independent"]
    independent = [down for _up, down, kind in records if kind == "independent"]
    if not records:
        return domain.ReplayPlan(
            complete=False,
            reason="尚未建立依赖图：无法给出最小子图，应保守重做当前章后续场景",
        )
    return domain.replay_subgraph(
        items, edges, changed=changed_subjects, independent=independent
    )


async def plan_local_replay(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    changed_subjects: Sequence[str],
    from_chapter_no: int,
    max_chapters: int = 3,
) -> tuple[domain.ReplayPlan, list[int], bool]:
    """纠错后的局部重演范围：固定为「改章起连续后续窗口」。

    依赖图 / 最小子图已移出生产主链（职责删减）。返回的计划恒为
    ``complete=False``，第三项恒为 ``True``（保守退路），避免对外宣称精确子图。

    ``max_chapters`` 是成本硬约束：一次纠错不得重演半本书。
    """
    del session, changed_subjects
    latest = int(work.latest_chapter_no)
    start = max(1, int(from_chapter_no))
    chapters = list(range(start, latest + 1))
    if len(chapters) > max_chapters:
        chapters = chapters[:max_chapters]
    plan = domain.ReplayPlan(
        complete=False,
        reason="conservative chapter window: replay from changed chapter forward",
        chapters=tuple(chapters),
    )
    return plan, chapters, True


async def invalidate_changed(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    changed_subjects: Sequence[str],
    reason: str,
    fallback_kinds: Sequence[str] = (),
) -> tuple[int, bool]:
    """按保守范围失效记忆：不再宣称最小子图（职责删减）。

    有 ``fallback_kinds`` 时只失效这些 kind；否则失效全部未失效条目中与
    ``changed_subjects`` 主体/实体相交者；若 subjects 为空则不失效。
    返回 ``(失效条数, True)``——第二项恒为保守退路。
    """
    subjects = [str(part).strip() for part in changed_subjects if str(part).strip()]
    if not subjects:
        return 0, False
    rows = {row.item_key: row for row in await _rows(session, work)}
    subject_set = set(subjects)
    if fallback_kinds:
        targets = {
            key
            for key, row in rows.items()
            if row.kind in fallback_kinds
        }
    else:
        targets = {
            key
            for key, row in rows.items()
            if subject_set & ({str(row.subject)} | {str(e) for e in (row.entities or [])})
        }
        # 无实体命中时仍保守：失效全部（等同旧「图不完整」分支但按主体收窄失败时）
        if not targets:
            targets = set(rows)
    now = datetime.now(UTC)
    count = 0
    for key in sorted(targets):
        row = rows.get(key)
        if row is None or row.invalidated_at is not None:
            continue
        row.invalidated_at = now
        row.invalidated_reason = (reason or "direction_changed")[:200]
        count += 1
    await session.flush()
    return count, True
