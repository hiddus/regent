"""改进库：写入、按范围检索、计数、回退、剪枝。

只有过了硬门交付的递归轮次才写入（见 overlay.record_verified_round），
模型的设想不进库。检索只看三种范围：全局、本书类型、本书自己。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.infrastructure.models import HarnessLibraryModel

# 试用中的条目还没过三章判定，只许它自己那本书看见：通用经验写的是
# scope=global，若不按状态收窄，别的书会引用一条随时可能被回退的配置，
# 回退就撤不回已经发生的影响。采纳后（adopted/superseded）恢复全局可见。
TRIAL_STATUS = "provisional"
SHAREABLE_STATUSES = ("candidate", "adopted", "superseded")
LIVE_STATUSES = (*SHAREABLE_STATUSES, TRIAL_STATUS)
SCOPES = frozenset({"global", "genre", "work"})


def _naive_utc(moment: datetime) -> datetime:
    """SQLite 读回无时区，Postgres 读回带时区：统一成无时区 UTC 再比较。"""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.replace(tzinfo=None)


def _uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def record_entry(
    session: AsyncSession,
    *,
    scope: str = "global",
    scope_key: str = "",
    genre_tag: str = "",
    chapter_role: str = "",
    failure_code: str = "",
    config_tree: dict[str, Any] | None = None,
    config_diff: dict[str, Any] | None = None,
    experience: str = "",
    evidence_refs: list[str] | None = None,
    parent_entry_id: uuid.UUID | str | None = None,
    source_work_id: uuid.UUID | str | None = None,
    source_run_id: uuid.UUID | str | None = None,
    harness_hash: str = "",
    status: str = "candidate",
    overlay_after: dict[str, Any] | None = None,
    since_chapter: int | None = None,
    baseline: dict[str, Any] | None = None,
) -> HarnessLibraryModel:
    if scope not in SCOPES:
        raise ValueError(f"未知范围: {scope}")
    entry = HarnessLibraryModel(
        id=uuid.uuid4(),
        scope=scope,
        scope_key=scope_key,
        genre_tag=genre_tag[:64],
        chapter_role=chapter_role[:64],
        failure_code=failure_code[:64],
        config_tree=config_tree or {},
        config_diff=config_diff or {},
        experience=experience,
        evidence_refs=evidence_refs or [],
        parent_entry_id=_uuid(parent_entry_id),
        source_work_id=_uuid(source_work_id),
        source_run_id=_uuid(source_run_id),
        harness_hash=harness_hash[:32],
        status=status,
        overlay_after=overlay_after,
        since_chapter=since_chapter,
        baseline=baseline or {},
        adopted_count=0,
        effective_count=0,
        rollback_reason="",
    )
    session.add(entry)
    await session.flush()
    return entry


async def search(
    session: AsyncSession,
    *,
    work_id: uuid.UUID | str | None = None,
    genre_tag: str = "",
    chapter_role: str = "",
    failure_code: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """全局条目、本类型条目、本书条目；别的书的本书条目和别人的试用条目都看不到。"""
    visible = [HarnessLibraryModel.scope == "global"]
    if genre_tag:
        visible.append(
            (HarnessLibraryModel.scope == "genre") & (HarnessLibraryModel.genre_tag == genre_tag)
        )
    if work_id:
        visible.append(
            (HarnessLibraryModel.scope == "work") & (HarnessLibraryModel.scope_key == str(work_id))
        )
    shareable = HarnessLibraryModel.status.in_(SHAREABLE_STATUSES)
    owner = _uuid(work_id)
    allowed = [shareable]
    if owner is not None:
        allowed.append(
            (HarnessLibraryModel.status == TRIAL_STATUS)
            & (HarnessLibraryModel.source_work_id == owner)
        )
    rows = (
        await session.scalars(
            select(HarnessLibraryModel).where(or_(*visible), or_(*allowed))
        )
    ).all()

    def score(row: HarnessLibraryModel) -> tuple[int, int, int]:
        points = 0
        if genre_tag and row.genre_tag == genre_tag:
            points += 3
        if chapter_role and row.chapter_role == chapter_role:
            points += 2
        if failure_code and row.failure_code == failure_code:
            points += 2
        if row.scope == "work":
            points += 1
        return points, int(row.effective_count or 0), int(row.adopted_count or 0)

    ranked = sorted(rows, key=score, reverse=True)[:limit]
    return [
        {
            "id": str(row.id),
            "scope": row.scope,
            "genre_tag": row.genre_tag,
            "chapter_role": row.chapter_role,
            "failure_code": row.failure_code,
            "experience": row.experience,
            "config_diff": row.config_diff,
            "adopted_count": row.adopted_count,
            "effective_count": row.effective_count,
        }
        for row in ranked
    ]


async def mark_adopted(session: AsyncSession, entry_id: uuid.UUID | str) -> None:
    row = await session.get(HarnessLibraryModel, _uuid(entry_id))
    if row is None:
        raise LookupError(f"改进库没有条目 {entry_id}")
    row.adopted_count = int(row.adopted_count or 0) + 1


async def mark_effective(session: AsyncSession, entry_id: uuid.UUID | str) -> None:
    """被引用的经验又帮一章过了硬门。"""
    row = await session.get(HarnessLibraryModel, _uuid(entry_id))
    if row is None:
        raise LookupError(f"改进库没有条目 {entry_id}")
    row.effective_count = int(row.effective_count or 0) + 1


async def record_rollback(
    session: AsyncSession, entry_id: uuid.UUID | str, *, reason: str = ""
) -> None:
    row = await session.get(HarnessLibraryModel, _uuid(entry_id))
    if row is None:
        raise LookupError(f"改进库没有条目 {entry_id}")
    row.status = "rolled_back"
    row.rollback_reason = reason
    row.decided_at = datetime.now(UTC)


async def prune_unused(
    session: AsyncSession,
    *,
    older_than_days: int = 30,
    now: datetime | None = None,
) -> int:
    """只退役放久了、从没被采纳也没被证明有效的经验；试用中和已采纳的不动。"""
    cutoff = _naive_utc(now or datetime.now(UTC)) - timedelta(days=older_than_days)
    rows = (
        await session.scalars(
            select(HarnessLibraryModel).where(
                HarnessLibraryModel.status == "candidate",
                HarnessLibraryModel.adopted_count == 0,
                HarnessLibraryModel.effective_count == 0,
            )
        )
    ).all()
    count = 0
    for row in rows:
        if row.created_at is None or _naive_utc(row.created_at) >= cutoff:
            continue
        row.status = "retired"
        row.rollback_reason = f"{older_than_days} 天内未被采纳"
        count += 1
    return count
