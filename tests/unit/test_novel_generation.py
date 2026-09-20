"""legacy_v1 执行体已退役；保留仍被导演链使用的辅助函数测试。"""

from types import SimpleNamespace

import pytest

from regent.novel.application.direction import ProductionStopped
from regent.novel.application.generation import (
    _chapter_assignment,
    _hard_quality_issues,
    _visible_performances,
    direct,
    perform,
    review,
    weave,
)
from regent.novel.application.works import _rewind_review_to_weave
from regent.novel.domain.states import ChapterStep, StepState


def test_private_character_reasoning_is_not_shared_with_director() -> None:
    run = SimpleNamespace(
        performances=[
            {
                "persona": "同伴",
                "private_reasoning": "我要隐瞒钥匙",
                "actions": ["收起手"],
                "dialogue": ["没什么"],
            }
        ]
    )
    visible = _visible_performances(run)
    assert "private_reasoning" not in visible[0]
    assert visible[0]["actions"] == ["收起手"]


@pytest.mark.asyncio
async def test_legacy_perform_direct_weave_review_are_retired() -> None:
    session = SimpleNamespace()
    work = SimpleNamespace(id="w")
    run = SimpleNamespace()
    provider = SimpleNamespace()
    for fn in (perform, direct, weave, review):
        with pytest.raises(ProductionStopped, match="legacy_v1"):
            await fn(session, provider=provider, work=work, run=run)


def test_chapter_assignment_still_defined_for_legacy_assemble_fallback() -> None:
    assert "objective" in _chapter_assignment(1)


def test_hard_quality_issues_flags_short_prose() -> None:
    issues = _hard_quality_issues("太短", [])
    assert any("800" in item for item in issues)


def test_rewind_review_to_weave_resets_legacy_steps() -> None:
    run = SimpleNamespace(
        review={"failure_classes": ["PROSE"], "prose_issues": ["节奏拖沓"]},
        generation_context={"architecture_version": "legacy_v1"},
    )
    by_name = {
        ChapterStep.PERFORM.value: SimpleNamespace(
            state=StepState.SUCCEEDED.value, attempt=1, error_code=""
        ),
        ChapterStep.DIRECT.value: SimpleNamespace(
            state=StepState.SUCCEEDED.value, attempt=1, error_code=""
        ),
        ChapterStep.WEAVE.value: SimpleNamespace(
            state=StepState.SUCCEEDED.value, attempt=1, error_code=""
        ),
        ChapterStep.REVIEW.value: SimpleNamespace(
            state=StepState.FAILED.value, attempt=1, error_code="QUALITY_GATE_FAILED"
        ),
    }
    _rewind_review_to_weave(run=run, by_name=by_name)
    assert by_name[ChapterStep.WEAVE.value].state == StepState.PENDING.value
    assert run.generation_context.get("revision_instructions")
