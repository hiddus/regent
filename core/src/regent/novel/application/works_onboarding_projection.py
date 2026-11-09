"""Onboarding ?????"""

from __future__ import annotations

from regent.novel.application.works_constants import OnboardingStatus
from regent.novel.domain.models import ClarifyQuestion, DirectionCard, OnboardingOut
from regent.novel.infrastructure.models import OnboardingSessionModel


def _onboarding_out(onboarding: OnboardingSessionModel) -> OnboardingOut:
    """根据会话内容投影引导状态。"""
    bible = dict(onboarding.world_bible or {})
    if bible.get("world_premise") or bible.get("personas"):
        status = OnboardingStatus.WORLD_REVIEW
    elif onboarding.directions:
        status = OnboardingStatus.DIRECTIONS
    elif onboarding.questions:
        status = OnboardingStatus.CLARIFYING
    else:
        status = OnboardingStatus.DIRECTIONS
    return OnboardingOut(
        status=status.value,
        clarify_round=int(onboarding.clarify_round or 0),
        question_count=int(onboarding.question_count or 0),
        questions=[ClarifyQuestion(**q) for q in (onboarding.questions or [])],
        assumptions=list(onboarding.assumptions or []),
        directions=[DirectionCard(**c) for c in (onboarding.directions or [])],
        world_bible=bible if status == OnboardingStatus.WORLD_REVIEW else None,
    )


onboarding_out = _onboarding_out
