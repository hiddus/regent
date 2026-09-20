"""一次性补丁：P1-4 审核裁定与申诉结论留痕。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/works.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


sub(
    """async def appeal_moderation(""",
    '''async def resolve_moderation(
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


async def appeal_moderation(''',
)

# 导出
sub(
    '    "report_fact",\n',
    '    "report_fact",\n    "resolve_appeal",\n    "resolve_moderation",\n',
)

path.write_text(text, encoding="utf-8")
print("patched", path)
