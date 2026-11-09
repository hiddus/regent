"""Work export services."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_constants import (
    AI_DISCLOSURE,
    CURRENT_EXPORT_NOTICE_VERSION,
)
from regent.novel.application.works_constants import (
    EXPORT_NOTICE_BODY as _EXPORT_NOTICE_BODY,
)
from regent.novel.application.works_constants import (
    EXPORT_NOTICE_TITLE as _EXPORT_NOTICE_TITLE,
)
from regent.novel.domain.errors import (
    ExportNoticeRequired,
    NotFound,
    ValidationFailed,
)
from regent.novel.domain.models import (
    ExportNoticeOut,
    ExportOut,
    ExportRequest,
)
from regent.novel.domain.states import (
    ChapterRunState,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ExportJobModel,
    ExportNoticeLogModel,
    ExportNoticeModel,
    StoryWorkModel,
)


async def get_export_notice(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> ExportNoticeOut:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ExportNoticeModel).where(
            ExportNoticeModel.user_id == owner_id,
            ExportNoticeModel.work_id == work_id,
        )
    )
    satisfied = row.satisfied_at if row else None
    current = row.notice_version == CURRENT_EXPORT_NOTICE_VERSION if row else False
    return ExportNoticeOut(
        notice_version=CURRENT_EXPORT_NOTICE_VERSION,
        satisfied_at=satisfied if current else None,
        required=not current or satisfied is None,
        title=_EXPORT_NOTICE_TITLE,
        body=_EXPORT_NOTICE_BODY,
    )


async def acknowledge_export_notice(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    notice_version: str,
) -> ExportNoticeOut:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if notice_version != CURRENT_EXPORT_NOTICE_VERSION:
        raise ValidationFailed("stale notice version")

    row = await session.scalar(
        select(ExportNoticeModel).where(
            ExportNoticeModel.user_id == owner_id,
            ExportNoticeModel.work_id == work_id,
        )
    )
    now = datetime.now(UTC)
    if row is None:
        row = ExportNoticeModel(
            id=uuid.uuid4(),
            user_id=owner_id,
            work_id=work_id,
            notice_version=notice_version,
            satisfied_at=now,
        )
        session.add(row)
    else:
        row.notice_version = notice_version
        row.satisfied_at = now
    session.add(
        ExportNoticeLogModel(
            id=uuid.uuid4(),
            user_id=owner_id,
            work_id=work_id,
            notice_version=notice_version,
            acknowledged_at=now,
        )
    )
    await session.flush()
    return ExportNoticeOut(
        notice_version=notice_version,
        satisfied_at=now,
        required=False,
        title=_EXPORT_NOTICE_TITLE,
        body=_EXPORT_NOTICE_BODY,
    )


async def export_work(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    payload: ExportRequest,
    base_url: str = "",
) -> ExportOut:
    """G-15 / G-22：先校验告知状态与格式白名单，再生成字节流（不经 LLM）。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)

    notice = await session.scalar(
        select(ExportNoticeModel).where(
            ExportNoticeModel.user_id == owner_id,
            ExportNoticeModel.work_id == work_id,
        )
    )
    if notice is None or not notice.satisfied_at:
        raise ExportNoticeRequired(CURRENT_EXPORT_NOTICE_VERSION)
    if notice.notice_version != CURRENT_EXPORT_NOTICE_VERSION:
        # 条款升级后必须重新告知，不能让老用户永久停留在旧版本
        raise ExportNoticeRequired(CURRENT_EXPORT_NOTICE_VERSION)

    runs = await session.scalars(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no)
    )
    chapters = [r for r in runs.all()]
    if payload.include_chapters:
        wanted = set(payload.include_chapters)
        chapters = [c for c in chapters if c.chapter_no in wanted]

    body = _render_export(work, chapters, payload.format)
    data = body.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()

    job = ExportJobModel(
        id=uuid.uuid4(),
        work_id=work_id,
        user_id=owner_id,
        format=payload.format,
        byte_size=len(data),
        content_sha256=digest,
        notice_version=CURRENT_EXPORT_NOTICE_VERSION,
        storage_key=f"novel-exports/{work_id}/{uuid.uuid4()}.{payload.format}",
    )
    session.add(job)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.exported",
        data={"format": payload.format, "byte_size": len(data), "sha256": digest},
        branch_id=work.branch_id,
    )
    return ExportOut(
        export_id=str(job.id),
        work_id=str(work_id),
        format=payload.format,
        byte_size=len(data),
        content_sha256=digest,
        ai_disclosure=AI_DISCLOSURE,
        download_url=f"{base_url}/v1/novel/exports/{job.id}/content",
    )


def _render_export(work: StoryWorkModel, chapters: list[ChapterRunModel], fmt: str) -> str:
    """确定性渲染：字节流不经过 LLM（G-15）。"""
    header = f"{work.title}\n\n{AI_DISCLOSURE}\n（本文件由 Novel Engine 导出，AI 参与生成）\n\n"
    parts: list[str] = [header]
    for ch in chapters:
        if fmt == "md":
            parts.append(f"## {ch.title}\n\n{ch.content}\n\n")
        else:
            parts.append(f"{ch.title}\n\n{ch.content}\n\n")
    if not chapters:
        parts.append("（尚无已完成章节）\n")
    return "".join(parts)


async def get_export_payload(
    session: AsyncSession, *, owner_id: uuid.UUID, export_id: uuid.UUID
) -> tuple[str, str]:
    """返回 (filename, text)。私有资源按 owner 过滤（G-12）。"""
    from regent.novel.infrastructure.models import NovelPrincipalModel

    principal = await session.get(NovelPrincipalModel, owner_id)
    if principal is None or principal.deleted_at is not None:
        raise NotFound("export not found")
    job = await session.scalar(
        select(ExportJobModel).where(
            ExportJobModel.id == export_id, ExportJobModel.user_id == owner_id
        )
    )
    if job is None:
        raise NotFound("export not found")
    work = await _get_owned_work(session, work_id=job.work_id, owner_id=owner_id)
    runs = await session.scalars(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == job.work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no)
    )
    return f"{work.title}.{job.format}", _render_export(work, list(runs.all()), job.format)
