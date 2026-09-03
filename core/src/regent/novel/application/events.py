"""持久事件与 SSE 补帧（Tech-Spec §9 / G-17）。

契约：
- 每部作品一条单调递增序列，无空洞。事件先落库再投递，断线可按 sequence 补帧。
- SSE ``id:`` 字段即 sequence；客户端用 Last-Event-ID 或 ``?after_seq=`` 续接。
- 请求的起点早于保留窗 → 返回 ``resync_required``，客户端改拉 snapshot。
- 数据查询失败必须抛错，不得吞异常后保持伪健康（heartbeat 只表示连接存活）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.domain.models import EVENT_SCHEMA_VERSION, EventPage, NovelEvent
from regent.novel.infrastructure.models import NovelEventModel

# 保留窗：早于此起点的补帧请求一律 resync（避免无界回溯 + 明确告知客户端）
DEFAULT_RETENTION_SEQUENCE_WINDOW = 5000
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000


async def append_event(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    event_type: str,
    data: dict[str, Any] | None = None,
    branch_id: uuid.UUID | None = None,
    chapter_no: int | None = None,
    decision_id: str | None = None,
    causation_id: str | None = None,
    correlation_id: str | None = None,
    event_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> NovelEvent:
    """在同一事务内分配序列并写入事件。

    序列分配使用 ``UPDATE ... RETURNING``（行锁），保证并发下单调递增且无空洞。
    """
    row = await session.execute(
        text(
            "INSERT INTO novel_work_sequences (work_id, last_sequence) "
            "VALUES (:work_id, 0) "
            "ON CONFLICT (work_id) DO UPDATE "
            "SET last_sequence = novel_work_sequences.last_sequence + 1 "
            "RETURNING last_sequence"
        ),
        {"work_id": work_id},
    )
    sequence = int(row.scalar_one())

    event = NovelEventModel(
        event_id=event_id or uuid.uuid4(),
        work_id=work_id,
        sequence=sequence,
        schema_version=EVENT_SCHEMA_VERSION,
        type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        branch_id=branch_id,
        chapter_no=chapter_no,
        decision_id=decision_id,
        causation_id=causation_id,
        correlation_id=correlation_id,
        data=data or {},
    )
    session.add(event)
    await session.flush()
    return _to_domain(event)


async def last_sequence(session: AsyncSession, work_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(NovelEventModel.sequence)
        .where(NovelEventModel.work_id == work_id)
        .order_by(NovelEventModel.sequence.desc())
        .limit(1)
    )
    return int(value or 0)


async def read_events(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    after_seq: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    retention_window: int = DEFAULT_RETENTION_SEQUENCE_WINDOW,
) -> EventPage:
    """按 sequence 读取增量事件。

    ``after_seq`` 早于保留窗下界时返回 ``resync_required=True`` 且不返回事件——
    客户端必须改拉 snapshot，不能假装拿到了完整增量。
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    head = await last_sequence(session, work_id)
    lower_bound = max(0, head - retention_window)
    if after_seq < lower_bound:
        return EventPage(events=[], last_sequence=head, has_more=False, resync_required=True)

    rows = await session.scalars(
        select(NovelEventModel)
        .where(NovelEventModel.work_id == work_id, NovelEventModel.sequence > after_seq)
        .order_by(NovelEventModel.sequence.asc())
        .limit(limit + 1)
    )
    events = [_to_domain(r) for r in rows.all()]
    has_more = len(events) > limit
    if has_more:
        events = events[:limit]
    return EventPage(
        events=events,
        last_sequence=events[-1].sequence if events else after_seq,
        has_more=has_more,
        resync_required=False,
    )


def _to_domain(row: NovelEventModel) -> NovelEvent:
    return NovelEvent(
        event_id=str(row.event_id),
        sequence=int(row.sequence),
        schema_version=int(row.schema_version),
        type=row.type,
        occurred_at=row.occurred_at,
        work_id=str(row.work_id),
        branch_id=str(row.branch_id) if row.branch_id else None,
        chapter_no=row.chapter_no,
        decision_id=row.decision_id,
        causation_id=row.causation_id,
        correlation_id=row.correlation_id,
        data=dict(row.data or {}),
    )


def sse_frame(event: NovelEvent) -> str:
    """序列化为 SSE 帧。``id:`` 必须是 sequence，客户端才能断点续传。"""
    import json

    payload = event.model_dump(mode="json")
    body = json.dumps(payload, ensure_ascii=False)
    return f"id: {event.sequence}\nevent: {event.type}\ndata: {body}\n\n"
