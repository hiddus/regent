"""人工裁决服务：从 works 抽出，供导演与 API 共用，避免 direction↔works 循环依赖。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.domain.errors import Conflict, NotFound, ValidationFailed
from regent.novel.domain.models import DecisionOption, DecisionView
from regent.novel.domain.states import (
    ChapterRunState,
    DecisionState,
    StoryWorkState,
    assert_chapter_run_transition,
    assert_story_work_transition,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    DecisionRequestModel,
    StoryWorkModel,
)


def decision_view(row: DecisionRequestModel) -> DecisionView:
    return DecisionView(
        decision_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        state=DecisionState(row.state),
        trigger_summary=row.trigger_summary,
        why_human=row.why_human,
        options=[DecisionOption(**o) for o in (row.options or [])],
        default_option_id=row.default_option_id or None,
        deadline=row.deadline,
        impact_level=row.impact_level,
        impact_horizon_chapters=int(row.impact_horizon_chapters),
        confirm_nonce=row.confirm_nonce,
        version=int(row.version),
    )


_decision_view = decision_view


async def get_owned_work(
    session: AsyncSession, *, work_id: uuid.UUID, owner_id: uuid.UUID
) -> StoryWorkModel:
    from regent.novel.application.works_access import get_owned_work as _get

    return await _get(session, work_id=work_id, owner_id=owner_id)


_get_owned_work = get_owned_work


async def create_decision(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
    run_id: uuid.UUID | None = None,
    node_id: str = "",
    trigger_summary: str,
    why_human: str,
    options: list[dict[str, object]],
    default_option_id: str,
    impact_level: str = "MEDIUM",
    impact_horizon_chapters: int = 1,
    deadline: datetime | None = None,
) -> DecisionView:
    """导演发起裁决：落持久化请求，章节与作品进入等待（P1-2 / G-13）。"""
    work = await get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if not options:
        raise ValidationFailed("decision requires at least one option")
    ids = {str(option.get("option_id", "")) for option in options}
    if default_option_id not in ids:
        raise ValidationFailed("default option must be one of the options")

    row = DecisionRequestModel(
        id=uuid.uuid4(),
        work_id=work_id,
        run_id=run_id,
        node_id=node_id or "",
        chapter_no=chapter_no,
        state=DecisionState.PENDING.value,
        trigger_summary=trigger_summary,
        why_human=why_human,
        options=[dict(option) for option in options],
        default_option_id=default_option_id,
        deadline=deadline,
        impact_level=impact_level,
        impact_horizon_chapters=impact_horizon_chapters,
        confirm_nonce=hashlib.sha256(
            f"{work_id}:{chapter_no}:{uuid.uuid4()}".encode()
        ).hexdigest()[:32],
    )
    session.add(row)
    await session.flush()

    if work.state == StoryWorkState.RUNNING.value:
        assert_story_work_transition(work.state, StoryWorkState.PENDING_DECISION.value)
        work.state = StoryWorkState.PENDING_DECISION.value
        work.version += 1
    if run_id is not None:
        run = await session.get(ChapterRunModel, run_id)
        if run is not None and run.state in (
            ChapterRunState.RUNNING.value,
            ChapterRunState.AWAITING_INPUT.value,
        ):
            assert_chapter_run_transition(
                run.state, ChapterRunState.PENDING_DECISION.value
            )
            run.state = ChapterRunState.PENDING_DECISION.value
            run.version += 1
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="decision.created",
        data={
            "decision_id": str(row.id),
            "chapter_no": chapter_no,
            "trigger_summary": trigger_summary,
            "default_option_id": default_option_id,
            "deadline": deadline.isoformat() if deadline else None,
        },
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        decision_id=str(row.id),
    )
    return decision_view(row)


async def list_pending_decisions(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID | None = None
) -> list[DecisionView]:
    """待裁决收件箱（FR-12）。"""
    stmt = (
        select(DecisionRequestModel)
        .join(StoryWorkModel, StoryWorkModel.id == DecisionRequestModel.work_id)
        .where(
            StoryWorkModel.owner_id == owner_id,
            DecisionRequestModel.state == DecisionState.PENDING.value,
        )
        .order_by(DecisionRequestModel.created_at.asc())
    )
    if work_id is not None:
        stmt = stmt.where(DecisionRequestModel.work_id == work_id)
    rows = await session.scalars(stmt)
    return [decision_view(row) for row in rows.all()]


async def apply_decision(
    session: AsyncSession,
    *,
    row: DecisionRequestModel,
    chosen: str,
    resolved_by: str,
) -> None:
    """裁决生效：结果回写章节，递增 input_version 并恢复推进（P1-2 / G-13）。"""
    from datetime import UTC

    from sqlalchemy import select, update

    from regent.novel.application.works_input import bump_input_version
    from regent.novel.domain.errors import Conflict

    result = await session.execute(
        update(DecisionRequestModel)
        .where(
            DecisionRequestModel.id == row.id,
            DecisionRequestModel.state == DecisionState.PENDING.value,
        )
        .values(
            state=DecisionState.RESOLVED.value,
            resolved_by=resolved_by[:32],
            resolved_option_id=chosen,
            resolved_at=datetime.now(UTC),
            version=DecisionRequestModel.version + 1,
        )
    )
    if result.rowcount != 1:
        raise Conflict("decision was resolved concurrently", current_version=int(row.version))
    await session.refresh(row)

    work = await session.get(StoryWorkModel, row.work_id)
    if work is not None and work.state == StoryWorkState.PENDING_DECISION.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
        work.version += 1

    run: ChapterRunModel | None = None
    if row.run_id is not None:
        run = await session.get(ChapterRunModel, row.run_id)
    if run is None and row.chapter_no is not None and work is not None:
        run = await session.scalar(
            select(ChapterRunModel)
            .where(
                ChapterRunModel.work_id == row.work_id,
                ChapterRunModel.branch_id == work.branch_id,
                ChapterRunModel.chapter_no == row.chapter_no,
            )
            .order_by(ChapterRunModel.attempt.desc())
            .limit(1)
        )
    if run is not None:
        if run.state == ChapterRunState.PENDING_DECISION.value:
            assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
            run.state = ChapterRunState.RUNNING.value
        context = dict(run.generation_context or {})
        resolutions = list(context.get("decision_resolutions") or [])
        chosen_option = next(
            (
                option
                for option in (row.options or [])
                if str(option.get("option_id")) == chosen
            ),
            None,
        )
        resolutions.append(
            {
                "decision_id": str(row.id),
                "option_id": chosen,
                "label": (chosen_option or {}).get("label", ""),
                "near_term_consequence": (chosen_option or {}).get(
                    "near_term_consequence", ""
                ),
                "reversibility": (chosen_option or {}).get("reversibility", ""),
                "trigger_summary": row.trigger_summary,
                "resolved_by": resolved_by,
                "chapter_no": row.chapter_no,
                "consumed": False,
            }
        )
        context["decision_resolutions"] = resolutions
        run.generation_context = context
        await bump_input_version(session, run=run, reason="decision_resolved")
    await session.flush()

    if work is not None:
        await append_event(
            session,
            work_id=row.work_id,
            event_type="decision.resolved",
            data={
                "decision_id": str(row.id),
                "option_id": chosen,
                "resolved_by": resolved_by,
            },
            branch_id=work.branch_id,
            chapter_no=row.chapter_no,
            decision_id=str(row.id),
        )


_apply_decision = apply_decision


async def sweep_expired_decisions(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 20
) -> list[str]:
    """到期未选择的裁决按默认项落定：与用户提交竞争，只有一个能赢（P1-2）。"""
    from datetime import UTC

    from regent.novel.domain.errors import Conflict

    moment = now or datetime.now(UTC)
    rows = (
        await session.scalars(
            select(DecisionRequestModel)
            .where(
                DecisionRequestModel.state == DecisionState.PENDING.value,
                DecisionRequestModel.deadline.is_not(None),
                DecisionRequestModel.deadline <= moment,
                DecisionRequestModel.default_option_id != "",
            )
            .limit(limit)
        )
    ).all()
    resolved: list[str] = []
    for row in rows:
        try:
            await apply_decision(
                session, row=row, chosen=row.default_option_id, resolved_by="timer"
            )
        except Conflict:
            continue
        resolved.append(str(row.id))
    return resolved

async def get_decision(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, decision_id: uuid.UUID
) -> DecisionView:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(DecisionRequestModel).where(
            DecisionRequestModel.id == decision_id,
            DecisionRequestModel.work_id == work_id,
        )
    )
    if row is None:
        raise NotFound("decision not found")
    return _decision_view(row)

async def resolve_decision(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    decision_id: uuid.UUID,
    option_id: str | None,
    accept_default: bool,
    confirm_nonce: str,
    resolved_by: str = "user",
) -> DecisionView:
    """G-13：裁决与默认 timer 竞争，条件更新保证仅一个结果成功。"""
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(DecisionRequestModel).where(
            DecisionRequestModel.id == decision_id,
            DecisionRequestModel.work_id == work_id,
        )
    )
    if row is None:
        raise NotFound("decision not found")
    if row.state != DecisionState.PENDING.value:
        raise Conflict(
            "decision already resolved",
            current_version=int(row.version),
            conflict_summary={"state": row.state},
        )
    if not row.confirm_nonce or confirm_nonce != row.confirm_nonce:
        raise ValidationFailed("confirm nonce mismatch")

    chosen = option_id
    if accept_default or not chosen:
        chosen = row.default_option_id
    if not chosen:
        raise ValidationFailed("no option selected and no default available")
    if chosen not in {o.get("option_id") for o in (row.options or [])}:
        raise ValidationFailed("unknown option")

    await _apply_decision(session, row=row, chosen=chosen, resolved_by=resolved_by)
    return _decision_view(row)

