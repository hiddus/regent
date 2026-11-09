"""Work lifecycle services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.domain.errors import (
    InvalidState,
)
from regent.novel.domain.states import (
    StoryWorkState,
)


async def soft_delete_work(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> None:
    """产品软删除。财务、授权与创作证据不级联物理删除（Tech-Spec §7）。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state == StoryWorkState.ARCHIVED.value:
        raise InvalidState("work already archived", current=work.state)
    work.deleted_at = datetime.now(UTC)
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.deleted",
        data={"soft": True, "retain": ["cost_entries", "export_notice_logs", "events"]},
        branch_id=work.branch_id,
    )
