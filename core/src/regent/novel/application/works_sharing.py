"""Work sharing services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.application.principal import as_utc
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_constants import (
    AI_DISCLOSURE,
)
from regent.novel.domain.errors import (
    NotFound,
)
from regent.novel.domain.models import (
    ShareOut,
)
from regent.novel.domain.states import (
    ChapterRunState,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ShareModel,
    StoryWorkModel,
)


async def create_share(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    scope: str = "FULL",
    from_chapter: int | None = None,
    to_chapter: int | None = None,
    expires_in_hours: int = 168,
    invitee_label: str = "",
    base_url: str = "",
) -> ShareOut:
    import secrets

    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    token = secrets.token_urlsafe(24)
    row = ShareModel(
        id=uuid.uuid4(),
        work_id=work_id,
        token=token,
        scope=scope,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        noindex=True,
        expires_at=datetime.now(UTC) + timedelta(hours=expires_in_hours),
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="share.created",
        data={"share_id": str(row.id), "scope": scope, "invitee": invitee_label},
        branch_id=None,
    )
    return ShareOut(
        share_id=str(row.id),
        work_id=str(work_id),
        share_url=f"{base_url}/read/{token}",
        scope=scope,
        noindex=True,
        expires_at=row.expires_at,
    )


async def revoke_share(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, share_id: uuid.UUID
) -> None:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ShareModel).where(ShareModel.id == share_id, ShareModel.work_id == work_id)
    )
    if row is None:
        raise NotFound("share not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.flush()
        await append_event(
            session,
            work_id=work_id,
            event_type="share.revoked",
            data={"share_id": str(share_id)},
        )


async def get_public_share(session: AsyncSession, *, token: str) -> dict[str, Any]:
    """Capability-link read path: no login, read-only, expiry/revoke fail closed."""
    row = await session.scalar(select(ShareModel).where(ShareModel.token == token))
    now = datetime.now(UTC)
    expires_at = as_utc(row.expires_at) if row is not None else None
    if row is None or row.revoked_at is not None or (expires_at is not None and expires_at <= now):
        raise NotFound("share not found")
    work = await session.get(StoryWorkModel, row.work_id)
    if work is None or work.deleted_at is not None:
        raise NotFound("share not found")
    query = (
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no)
    )
    if row.from_chapter is not None:
        query = query.where(ChapterRunModel.chapter_no >= row.from_chapter)
    if row.to_chapter is not None:
        query = query.where(ChapterRunModel.chapter_no <= row.to_chapter)
    chapters = (await session.scalars(query)).all()
    return {
        "title": work.title,
        "genre": work.genre,
        "ai_disclosure": AI_DISCLOSURE,
        "expires_at": row.expires_at,
        "chapters": [
            {
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "content": chapter.content,
                "word_count": chapter.word_count,
            }
            for chapter in chapters
        ],
    }
