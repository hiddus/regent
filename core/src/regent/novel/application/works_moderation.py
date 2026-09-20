"""Work moderation services."""

from __future__ import annotations

from regent.novel.application.works_access import get_owned_work as _get_owned_work

REPORT_REASON_CODES = frozenset(
    {
        "illegal",
        "porn",
        "violence",
        "political",
        "infringement",
        "privacy",
        "other",
        "rule_hit",
    }
)

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.domain.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from regent.novel.domain.models import (
    ModerationCaseOut,
    ModerationDecision,
)
from regent.novel.domain.moderation import scan_text
from regent.novel.domain.states import (
    ChapterRunState,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ModerationCaseModel,
)


async def report_moderation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int | None = None,
    reason_code: str = "other",
    detail: str = "",
) -> ModerationCaseOut:
    """投诉/举报入口（FR-25 / G-23）。

    任何投诉都必须落 ModerationCase；没有结论的案件不得视为“已处理”，
    因此新建案件一律是 PENDING，由人工或第三方审核给出结论。
    """
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if reason_code not in REPORT_REASON_CODES:
        raise ValidationFailed(f"unsupported reason_code: {reason_code}")
    if chapter_no is not None:
        exists = await session.scalar(
            select(ChapterRunModel.id).where(
                ChapterRunModel.work_id == work_id,
                ChapterRunModel.chapter_no == chapter_no,
            )
        )
        if exists is None:
            raise NotFound("chapter not found")
    row = ModerationCaseModel(
        id=uuid.uuid4(),
        work_id=work_id,
        chapter_no=chapter_no,
        target_type="CHAPTER" if chapter_no is not None else "WORK",
        decision=ModerationDecision.PENDING.value,
        reason_code=reason_code,
        evidence_ref=detail[:255],
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.reported",
        data={"case_id": str(row.id), "reason_code": reason_code, "detail": detail[:200]},
        chapter_no=chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(work_id),
        chapter_no=chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision.PENDING,
        reason_code=reason_code,
        detail=detail,
    )


async def scan_chapter_rules(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
) -> dict[str, object]:
    """按配置词表扫描章节，产出疑似命中案件。

    只提名、不判定：命中生成 PENDING 案件等待人工/第三方结论。
    未配置词表时返回 configured=False，不产生“通过”含义。
    """
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.chapter_no == chapter_no,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.attempt.desc())
    )
    if run is None:
        raise NotFound("canonized chapter not found")
    result = scan_text(run.content or "")
    if not result.configured:
        return {"configured": False, "scanned": False, "hits": 0, "cases": []}
    cases: list[str] = []
    for hit in result.hits:
        row = ModerationCaseModel(
            id=uuid.uuid4(),
            work_id=work_id,
            chapter_no=chapter_no,
            target_type="CHAPTER",
            decision=ModerationDecision.PENDING.value,
            reason_code="rule_hit",
            evidence_ref=f"offset:{hit.offset}",
        )
        session.add(row)
        cases.append(str(row.id))
    if cases:
        await session.flush()
        await append_event(
            session,
            work_id=work_id,
            event_type="moderation.scanned",
            data={"chapter_no": chapter_no, "hits": len(cases)},
            chapter_no=chapter_no,
        )
    return {
        "configured": True,
        "scanned": True,
        "hits": len(cases),
        "cases": cases,
    }


async def list_moderation_cases(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> list[ModerationCaseOut]:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    rows = await session.scalars(
        select(ModerationCaseModel)
        .where(ModerationCaseModel.work_id == work_id)
        .order_by(ModerationCaseModel.created_at.desc())
    )
    return [
        ModerationCaseOut(
            case_id=str(r.id),
            work_id=str(r.work_id),
            chapter_no=r.chapter_no,
            target_type=r.target_type,
            decision=ModerationDecision(r.decision),
            reason_code=r.reason_code or None,
            detail=r.evidence_ref,
            appealed_at=r.appealed_at,
            resolved_at=r.resolved_at,
        )
        for r in rows.all()
    ]


async def resolve_moderation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    case_id: uuid.UUID,
    decision: ModerationDecision,
    reason_code: str = "",
    evidence: str = "",
    actor: str = "moderator",
) -> ModerationCaseOut:
    """给出审核结论（P1-4 / G-23）。

    无结论不得视为通过，因此结论必须落库并留痕。作者不能给自己的待审案件
    下"通过"结论——那是把审核变成自我放行。
    """
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if decision not in (ModerationDecision.APPROVED, ModerationDecision.REJECTED):
        raise ValidationFailed("moderation decision must be APPROVED or REJECTED")
    row = await session.scalar(
        select(ModerationCaseModel).where(
            ModerationCaseModel.id == case_id, ModerationCaseModel.work_id == work_id
        )
    )
    if row is None:
        raise NotFound("moderation case not found")
    if row.resolved_at is not None:
        raise Conflict("case already resolved", current_version=1)
    if actor == "author" and decision is ModerationDecision.APPROVED:
        raise PermissionDenied("作者不能对自己的案件给出通过结论")
    if reason_code and reason_code not in REPORT_REASON_CODES:
        raise ValidationFailed(f"unsupported reason_code: {reason_code}")

    row.decision = decision.value
    if reason_code:
        row.reason_code = reason_code
    if evidence:
        row.evidence_ref = evidence[:255]
    row.resolved_at = datetime.now(UTC)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.resolved",
        data={
            "case_id": str(case_id),
            "decision": decision.value,
            "reason_code": reason_code,
            "actor": actor,
        },
        chapter_no=row.chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision(row.decision),
        reason_code=row.reason_code or None,
        detail=row.evidence_ref,
        appealed_at=row.appealed_at,
        resolved_at=row.resolved_at,
    )


async def resolve_appeal(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    case_id: uuid.UUID,
    upheld: bool,
    reason_code: str = "",
    evidence: str = "",
    actor: str = "moderator",
) -> ModerationCaseOut:
    """申诉结论：成立则恢复，维持则保留原判定；两种结果都要留痕（P1-4）。"""
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ModerationCaseModel).where(
            ModerationCaseModel.id == case_id, ModerationCaseModel.work_id == work_id
        )
    )
    if row is None:
        raise NotFound("moderation case not found")
    if row.appealed_at is None:
        raise Conflict("case has not been appealed", current_version=1)
    if row.resolved_at is not None:
        raise Conflict("appeal already resolved", current_version=1)
    row.decision = (
        ModerationDecision.REJECTED.value if upheld else ModerationDecision.APPROVED.value
    )
    if reason_code:
        row.reason_code = reason_code
    if evidence:
        row.evidence_ref = evidence[:255]
    row.resolved_at = datetime.now(UTC)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.appeal_resolved",
        data={
            "case_id": str(case_id),
            "upheld": upheld,
            "decision": row.decision,
            "actor": actor,
        },
        chapter_no=row.chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision(row.decision),
        reason_code=row.reason_code or None,
        detail=row.evidence_ref,
        appealed_at=row.appealed_at,
        resolved_at=row.resolved_at,
    )


async def appeal_moderation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    case_id: uuid.UUID,
    reason: str,
) -> ModerationCaseOut:
    """误判申诉必须留痕（G-23）。"""
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ModerationCaseModel).where(
            ModerationCaseModel.id == case_id, ModerationCaseModel.work_id == work_id
        )
    )
    if row is None:
        raise NotFound("moderation case not found")
    if row.appealed_at is not None:
        raise Conflict("case already appealed", current_version=1)
    row.appealed_at = datetime.now(UTC)
    row.appeal_reason = reason
    row.decision = ModerationDecision.APPEALED.value
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.appealed",
        data={"case_id": str(case_id), "reason": reason[:200]},
        chapter_no=row.chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision(row.decision),
        reason_code=row.reason_code or None,
        detail=row.evidence_ref,
        appealed_at=row.appealed_at,
        resolved_at=row.resolved_at,
    )
