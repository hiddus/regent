"""World bible services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.application.production import TransactionFreeProvider
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_constants import (
    OnboardingStatus,
)
from regent.novel.application.works_onboarding_projection import onboarding_out as _onboarding_out
from regent.novel.domain.errors import (
    InvalidState,
    NotFound,
    ValidationFailed,
)
from regent.novel.domain.models import (
    DirectionCard,
    OnboardingOut,
    WorldBibleOut,
)
from regent.novel.domain.states import (
    StoryWorkState,
    assert_story_work_transition,
)
from regent.novel.infrastructure.models import (
    OnboardingSessionModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)


async def get_world_bible(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> WorldBibleOut:
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.story_bible_locked_at and (work.story_bible or {}):
        return WorldBibleOut(
            status="locked",
            world_bible=dict(work.story_bible or {}),
            locked_at=work.story_bible_locked_at,
        )
    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    draft = dict((onboarding.world_bible if onboarding else None) or {})
    if not draft:
        raise NotFound("world bible not found")
    return WorldBibleOut(status="draft", world_bible=draft, locked_at=None)


async def revise_world_bible(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    notes: str,
    provider: Any | None = None,
) -> OnboardingOut:
    """按用户意见让编剧重写世界书草稿；仍停在 WORLD_REVIEW。"""
    if provider is not None and not isinstance(provider, TransactionFreeProvider):
        provider = TransactionFreeProvider(provider, session)
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state != StoryWorkState.ONBOARDING.value or work.story_bible_locked_at:
        raise InvalidState("world bible already locked", current=work.state)
    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    if onboarding is None:
        raise NotFound("onboarding session not found")
    prev = dict(onboarding.world_bible or {})
    if not prev:
        raise InvalidState("confirm direction first", current=work.state)
    if provider is None:
        raise ValidationFailed("provider required to revise world bible")

    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    from regent.novel.application.generation import generate_world_bible
    from regent.novel.domain.story_direction import (
        keywords_from_assumptions,
        locked_direction_from_assumptions,
    )

    assumptions = list((goal.assumptions if goal else None) or [])
    bible = await generate_world_bible(
        provider,
        raw_intent=(goal.raw_intent if goal else "") or "",
        genre=work.genre or "",
        direction_keywords=keywords_from_assumptions(assumptions),
        locked_direction=locked_direction_from_assumptions(assumptions),
        revise_notes=notes.strip(),
        previous_bible=prev,
    )
    onboarding.world_bible = bible.model_dump(mode="json")
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="onboarding.world_bible_revised",
        data={"notes": notes.strip()[:200]},
        branch_id=work.branch_id,
    )
    return _onboarding_out(onboarding)


async def lock_world_bible(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
) -> tuple[StoryWorkModel, OnboardingOut]:
    """用户确认世界书 → 写入正史与角色表 → READY。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state != StoryWorkState.ONBOARDING.value:
        if work.story_bible_locked_at:
            onboarding = await session.scalar(
                select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
            )
            return work, OnboardingOut(
                status=OnboardingStatus.READY.value,
                world_bible=dict(work.story_bible or {}),
                assumptions=list((onboarding.assumptions if onboarding else None) or []),
                directions=[
                    DirectionCard(**c)
                    for c in ((onboarding.directions if onboarding else None) or [])
                ],
            )
        raise InvalidState("cannot lock world bible", current=work.state)

    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    if onboarding is None:
        raise NotFound("onboarding session not found")
    draft = dict(onboarding.world_bible or {})
    if not draft:
        raise ValidationFailed("world bible draft missing")

    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    from regent.novel.domain.story_direction import keywords_from_assumptions
    from regent.novel.domain.world_bible import WorldBible, bible_validation_issues

    kw = keywords_from_assumptions(list((goal.assumptions if goal else None) or []))
    issues = bible_validation_issues(draft, direction_keywords=kw)
    if issues:
        raise ValidationFailed("; ".join(issues))

    bible = WorldBible.model_validate(draft)
    now = datetime.now(UTC)
    dumped = bible.model_dump(mode="json")
    work.story_bible = dumped
    work.story_bible_locked_at = now
    onboarding.world_bible = dumped
    onboarding.world_bible_locked_at = now

    existing = {
        p.name: p
        for p in (
            await session.scalars(
                select(PersonaSpecModel).where(PersonaSpecModel.work_id == work_id)
            )
        ).all()
    }
    for persona in bible.personas:
        identity = {
            "role": persona.identity or persona.bio[:80],
            "kind": persona.kind,
            "bio": persona.bio,
        }
        entry = {
            "identity": identity,
            "drives": {"primary": persona.drives},
            "voice": {"style": persona.voice},
        }
        if persona.name in existing:
            row = existing[persona.name]
            row.identity = identity
            row.drives = entry["drives"]
            row.voice = entry["voice"]
            row.stable_traits = [persona.drives, persona.voice]
        else:
            session.add(
                PersonaSpecModel(
                    id=uuid.uuid4(),
                    work_id=work_id,
                    name=persona.name,
                    stable_traits=[persona.drives, persona.voice],
                    **entry,
                )
            )

    assert_story_work_transition(work.state, StoryWorkState.READY.value)
    work.state = StoryWorkState.READY.value
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.world_bible_locked",
        data={"persona_count": len(bible.personas)},
        branch_id=work.branch_id,
    )
    return work, OnboardingOut(
        status=OnboardingStatus.READY.value,
        clarify_round=int(onboarding.clarify_round or 0),
        question_count=0,
        questions=[],
        assumptions=list(onboarding.assumptions or []),
        directions=[DirectionCard(**c) for c in (onboarding.directions or [])],
        world_bible=dumped,
    )


async def ensure_legacy_story_bible(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    provider: Any | None = None,
) -> None:
    """遗留 READY 作品无世界书时，开跑前一次性补齐并自动锁定。"""
    if provider is not None and not isinstance(provider, TransactionFreeProvider):
        provider = TransactionFreeProvider(provider, session)
    if work.story_bible_locked_at and (work.story_bible or {}):
        return
    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work.id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    if goal is None:
        return
    if provider is None:
        # 旧作品在引入 WorldBible 前已经过 READY 门槛；没有模型时不能永久卡死。
        # 只封存已有前提，不伪造新设定；后续上下文仍按 legacy fallback 读取。
        work.story_bible = {
            "legacy_compat": True,
            "world_premise": str(goal.raw_intent or ""),
            "underlying_rules": [],
            "personas": [],
        }
        work.story_bible_locked_at = datetime.now(UTC)
        await session.flush()
        return
    from regent.novel.application.generation import generate_world_bible
    from regent.novel.domain.story_direction import (
        keywords_from_assumptions,
        locked_direction_from_assumptions,
    )

    assumptions = list(goal.assumptions or [])
    bible = await generate_world_bible(
        provider,
        raw_intent=goal.raw_intent,
        genre=work.genre or "",
        direction_keywords=keywords_from_assumptions(assumptions),
        locked_direction=locked_direction_from_assumptions(assumptions),
    )
    dumped = bible.model_dump(mode="json")
    work.story_bible = dumped
    work.story_bible_locked_at = datetime.now(UTC)
    existing_names = set(
        (
            await session.scalars(
                select(PersonaSpecModel.name).where(PersonaSpecModel.work_id == work.id)
            )
        ).all()
    )
    for persona in bible.personas:
        if persona.name in existing_names:
            continue
        session.add(
            PersonaSpecModel(
                id=uuid.uuid4(),
                work_id=work.id,
                name=persona.name,
                identity={
                    "role": persona.identity or persona.bio[:80],
                    "kind": persona.kind,
                    "bio": persona.bio,
                },
                drives={"primary": persona.drives},
                voice={"style": persona.voice},
                stable_traits=[persona.drives, persona.voice],
            )
        )
    await session.flush()
