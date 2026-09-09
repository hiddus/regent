"""P1-4：审核/申诉结果留痕与「不逐场审核也能完成首章」的旅程验收。

- 无结论不得视为通过：案件必须能被裁定，结论要落库并留痕；
- 作者不能给自己的案件下"通过"结论（那等于自我放行）；
- 申诉必须有结论，成立与维持两种结果都要留痕。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import works
from regent.novel.domain.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from regent.novel.domain.states import ChapterRunState, ModerationDecision
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ModerationCaseModel,
    NovelPrincipalModel,
    StoryWorkModel,
)
from sqlalchemy import select


async def _work(session):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"moderation-test:{owner}"))
    work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑")
    session.add(work)
    await session.flush()
    session.add(
        ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            state=ChapterRunState.CANONIZED.value,
            content="正文内容" * 200,
        )
    )
    await session.flush()
    return owner, work


async def _case(session, work, *, decision=ModerationDecision.PENDING.value):
    row = ModerationCaseModel(
        id=uuid.uuid4(),
        work_id=work.id,
        chapter_no=1,
        target_type="CHAPTER",
        decision=decision,
        reason_code="rule_hit",
        evidence_ref="offset:12",
    )
    session.add(row)
    await session.flush()
    return row


async def test_resolution_is_recorded(novel_db, monkeypatch):
    """结论必须落库并留痕：没有结论不得视为通过。"""
    events: list[dict] = []

    async def _record(session, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(works, "append_event", _record)

    async with novel_db() as session:
        owner, work = await _work(session)
        row = await _case(session, work)
        await session.commit()
        case_id = row.id

    async with novel_db() as session:
        out = await works.resolve_moderation(
            session,
            owner_id=owner,
            work_id=work.id,
            case_id=case_id,
            decision=ModerationDecision.REJECTED,
            reason_code="rule_hit",
            evidence="offset:12",
        )
        assert out.decision == ModerationDecision.REJECTED
        assert out.resolved_at is not None
        await session.commit()

    async with novel_db() as session:
        stored = await session.scalar(select(ModerationCaseModel))
        assert stored.decision == ModerationDecision.REJECTED.value
        assert stored.resolved_at is not None
    assert any(e.get("event_type") == "moderation.resolved" for e in events)


async def test_author_cannot_approve_own_case(novel_db, monkeypatch):
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work = await _work(session)
        row = await _case(session, work)
        case_id = row.id
        with pytest.raises(PermissionDenied):
            await works.resolve_moderation(
                session,
                owner_id=owner,
                work_id=work.id,
                case_id=case_id,
                decision=ModerationDecision.APPROVED,
                actor="author",
            )


async def test_only_approval_or_rejection_is_a_conclusion(novel_db, monkeypatch):
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work = await _work(session)
        row = await _case(session, work)
        with pytest.raises(ValidationFailed):
            await works.resolve_moderation(
                session,
                owner_id=owner,
                work_id=work.id,
                case_id=row.id,
                decision=ModerationDecision.PENDING,
            )


async def test_double_resolution_is_rejected(novel_db, monkeypatch):
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work = await _work(session)
        row = await _case(session, work)
        await works.resolve_moderation(
            session,
            owner_id=owner,
            work_id=work.id,
            case_id=row.id,
            decision=ModerationDecision.APPROVED,
        )
        with pytest.raises(Conflict):
            await works.resolve_moderation(
                session,
                owner_id=owner,
                work_id=work.id,
                case_id=row.id,
                decision=ModerationDecision.REJECTED,
            )


async def test_appeal_outcome_is_recorded(novel_db, monkeypatch):
    """申诉成立与维持都要有结论：不能申诉完就没人管。"""
    events: list[dict] = []

    async def _record(session, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(works, "append_event", _record)

    async with novel_db() as session:
        owner, work = await _work(session)
        row = await _case(session, work, decision=ModerationDecision.REJECTED.value)
        await works.appeal_moderation(
            session,
            owner_id=owner,
            work_id=work.id,
            case_id=row.id,
            reason="这是误判",
        )
        await session.commit()
        case_id = row.id

    async with novel_db() as session:
        out = await works.resolve_appeal(
            session,
            owner_id=owner,
            work_id=work.id,
            case_id=case_id,
            upheld=False,
        )
        assert out.decision == ModerationDecision.APPROVED, "申诉成立应恢复"
        await session.commit()

    assert any(e.get("event_type") == "moderation.appeal_resolved" for e in events)

    async with novel_db() as session:
        stored = await session.scalar(select(ModerationCaseModel))
        assert stored.resolved_at is not None
        # 已结案件不得再次申诉结论
        with pytest.raises(Conflict):
            await works.resolve_appeal(
                session,
                owner_id=owner,
                work_id=work.id,
                case_id=case_id,
                upheld=True,
            )


async def test_appeal_requires_an_appealed_case(novel_db, monkeypatch):
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work = await _work(session)
        row = await _case(session, work)
        with pytest.raises(Conflict, match="not been appealed"):
            await works.resolve_appeal(
                session,
                owner_id=owner,
                work_id=work.id,
                case_id=row.id,
                upheld=True,
            )


async def test_unknown_case_is_not_found(novel_db, monkeypatch):
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work = await _work(session)
        with pytest.raises(NotFound):
            await works.resolve_moderation(
                session,
                owner_id=owner,
                work_id=work.id,
                case_id=uuid.uuid4(),
                decision=ModerationDecision.APPROVED,
            )
