from types import SimpleNamespace

import pytest

from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application.generation import (
    ChapterDraft,
    ChapterReview,
    DirectorPlan,
    _chapter_assignment,
    _hard_quality_issues,
    _visible_performances,
    direct,
    review,
    weave,
)
from regent.novel.application.works import _rewind_review_to_weave
from regent.novel.domain.states import ChapterStep, StepState


class FakeProvider:
    def __init__(self, *, review_passed: bool = True) -> None:
        self.review_passed = review_passed
        self.calls: list[str] = []
        self.requests: list[dict] = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.calls.append(response_model.__name__)
        self.requests.append({"response_model": response_model, **kwargs})
        if response_model is DirectorPlan:
            output = DirectorPlan(
                scene_goal="主角必须拿到线索",
                beats=["遇阻", "试探", "付出代价"],
                ending_hook="旧表开始倒走",
                state_before={"location": "车站", "clue": "未知"},
                state_after={"location": "旧宅", "clue": "已获得"},
            )
        elif response_model is ChapterReview:
            review_number = self.calls.count("ChapterReview")
            output = ChapterReview(
                passed=self.review_passed or review_number > 1,
                prose_issues=[] if self.review_passed or review_number > 1 else ["因果跳跃"],
                revision_instructions=[] if self.review_passed or review_number > 1 else ["补足取得线索的代价"],
            )
        elif response_model is ChapterDraft:
            marker = "修订稿" if "ChapterReview" in self.calls else "初稿"
            output = ChapterDraft(title=marker, content=marker + "正文推进" * 220)
        else:
            raise AssertionError(response_model)
        return StructuredModelResponse(
            output=output,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
            model="fake",
        )


class FakeSession:
    def __init__(self) -> None:
        self.rows = []

    async def scalar(self, _):
        return None

    async def scalars(self, _):
        class _R:
            def all(self):
                return []
        return _R()

    def add(self, row) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        return None


def test_private_character_reasoning_is_not_shared_with_director() -> None:
    run = SimpleNamespace(performances=[{
        "persona": "同伴", "private_reasoning": "我要隐瞒钥匙",
        "actions": ["收起手"], "dialogue": ["没什么"],
    }])
    visible = _visible_performances(run)
    assert "private_reasoning" not in visible[0]
    assert visible[0]["actions"] == ["收起手"]


async def test_agent_loop_directs_then_weaves_readable_chapter() -> None:
    provider = FakeProvider()
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=1,
        generation_context={"goal": "找到父亲", "raw_intent": "", "normalized_goal": "",
                            "canon": [], "target_node": {}, "prev_node": {}, "next_node": {}},
        performances=[{"persona": "主角", "actions": ["追查旧表"]}],
        user_guidance={},
        title="",
        content="",
        word_count=0,
        review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4(), genre="东方玄幻")
    session = FakeSession()
    await direct(session, provider=provider, work=work, run=run)
    await weave(session, provider=provider, work=work, run=run)
    assert run.generation_context["director_plan"]["ending_hook"] == "旧表开始倒走"
    assert run.word_count >= 600
    assert provider.calls == ["DirectorPlan", "ChapterDraft"]
    assert provider.requests[-1]["temperature"] == 0.85


async def test_weave_consumes_review_instructions_for_a_fresh_draft() -> None:
    provider = FakeProvider()
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=2,
        generation_context={
            "director_plan": {
                "state_before": {"location": "车站"},
                "state_after": {"location": "旧宅"},
            },
            "revision_instructions": ["补足取得线索的代价", "删除重复威胁台词"],
        },
        performances=[], user_guidance={}, title="旧稿", content="旧稿", word_count=2,
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4(), genre="东方玄幻")

    await weave(FakeSession(), provider=provider, work=work, run=run)

    request = provider.requests[-1]
    assert request["temperature"] == 0.85
    assert "补足取得线索的代价" in request["system_prompt"]
    assert "删除重复威胁台词" in request["user_prompt"]
    assert "revision_instructions" not in run.generation_context


def test_failed_quality_review_rewinds_to_weave_with_review_evidence() -> None:
    weave_step = SimpleNamespace(state=StepState.SUCCEEDED.value, error_code="old")
    run = SimpleNamespace(
        review={
            "revision_instructions": ["补足线索代价"],
            "prose_issues": ["告知而非展示"],
            "failure_classes": ["PROSE"],
        },
        generation_context={"director_plan": {"scene_goal": "取得线索"}},
    )

    _rewind_review_to_weave(
        run=run,
        by_name={ChapterStep.WEAVE.value: weave_step},
    )

    assert weave_step.state == StepState.PENDING.value
    assert weave_step.error_code == ""
    assert run.generation_context["revision_instructions"] == ["补足线索代价"]


@pytest.mark.parametrize(
    ("failure_class", "expected_pending"),
    [
        ("STRUCTURE", {"DIRECT", "WEAVE"}),
        ("PERFORMANCE", {"PERFORM", "DIRECT", "WEAVE"}),
    ],
)
def test_quality_failure_rewinds_to_its_owning_layer(
    failure_class: str, expected_pending: set[str]
) -> None:
    by_name = {
        step.value: SimpleNamespace(state=StepState.SUCCEEDED.value, error_code="old")
        for step in (ChapterStep.PERFORM, ChapterStep.DIRECT, ChapterStep.WEAVE)
    }
    run = SimpleNamespace(
        review={"failure_classes": [failure_class], "prose_issues": ["issue"]},
        generation_context={},
    )

    _rewind_review_to_weave(run=run, by_name=by_name)

    actual_pending = {
        name for name, row in by_name.items() if row.state == StepState.PENDING.value
    }
    assert actual_pending == expected_pending


async def test_failed_review_triggers_evidence_based_revision() -> None:
    provider = FakeProvider(review_passed=False)
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=1,
        generation_context={"goal": "找到父亲", "director_plan": {
            "state_before": {"location": "车站", "clue": "未知"},
            "state_after": {"location": "旧宅", "clue": "已获得"},
        }},
        performances=[],
        user_guidance={},
        title="初稿",
        content="初稿。" * 300,
        word_count=900,
        review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4(), genre="东方玄幻")
    await review(FakeSession(), provider=provider, work=work, run=run)
    assert run.title == "修订稿"
    assert run.review["passed"] is True
    assert provider.calls == ["ChapterReview", "ChapterDraft", "ChapterReview"]


def test_rolling_assignment_gives_each_chapter_a_distinct_job() -> None:
    roles = [_chapter_assignment(number)["role"] for number in (1, 2, 3)]
    assert roles == ["进入与施压", "升级与转向", "兑现与转场"]


def test_hard_gate_detects_cross_chapter_repetition() -> None:
    prior = "韩墨拔剑逼近，青色剑影锁定后心。" * 80
    draft = prior + "局势终于发生变化。" * 20
    issues = _hard_quality_issues(draft, [{"chapter_no": 21, "content": prior}])
    assert any("第21章" in issue and "复写" in issue for issue in issues)


def test_hard_gate_detects_repeated_template_phrases_across_window() -> None:
    chapters = [
        {"chapter_no": number, "content": ("咬破舌尖后继续向前。" * 5) + ("新的具体事件发生。" * 120)}
        for number in (20, 21)
    ]
    draft = ("他瞳孔骤缩，却不再犹豫。" * 5) + ("局势继续发生新的变化。" * 120)
    issues = _hard_quality_issues(draft, chapters)
    assert any("模板套句密度过高" in issue for issue in issues)


async def test_revision_that_still_fails_review_is_not_force_passed() -> None:
    provider = FakeProvider(review_passed=False)

    async def always_fail(*, response_model, **kwargs):
        if response_model is ChapterReview:
            provider.calls.append("ChapterReview")
            output = ChapterReview(passed=False, prose_issues=["仍未推进"])
            return StructuredModelResponse(
                output=output, usage=ModelUsage(input_tokens=1, output_tokens=1), model="fake",
            )
        return await FakeProvider.generate_structured(provider, response_model=response_model, **kwargs)

    provider.generate_structured = always_fail
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=2,
        generation_context={"recent_chapters": [], "director_plan": {
            "state_before": {"location": "车站", "clue": "未知"},
            "state_after": {"location": "旧宅", "clue": "已获得"},
        }}, performances=[], user_guidance={},
        title="初稿", content="有效正文" * 220, word_count=880, review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4(), genre="东方玄幻")
    with pytest.raises(RuntimeError, match="QUALITY_GATE_FAILED"):
        await review(FakeSession(), provider=provider, work=work, run=run)
    assert provider.calls == ["ChapterReview", "ChapterDraft", "ChapterReview"]


async def test_persistent_leakage_cannot_degraded_pass_by_word_count() -> None:
    provider = FakeProvider(review_passed=False)

    async def leakage_provider(*, response_model, **kwargs):
        provider.calls.append(response_model.__name__)
        if response_model is ChapterReview:
            output = ChapterReview(
                passed=False,
                leakage_issues=["配角说出了未被授予的密室位置"],
                revision_instructions=["重新按角色信息集生成行动"],
            )
        elif response_model is ChapterDraft:
            output = ChapterDraft(title="长修订稿", content="有效推进" * 450)
        else:
            return await FakeProvider.generate_structured(
                provider, response_model=response_model, **kwargs
            )
        return StructuredModelResponse(
            output=output,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
            model="fake",
        )

    provider.generate_structured = leakage_provider
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=2,
        generation_context={
            "recent_chapters": [],
            "director_plan": {
                "state_before": {"location": "车站", "clue": "未知"},
                "state_after": {"location": "旧宅", "clue": "已获得"},
            },
        },
        performances=[], user_guidance={}, title="初稿",
        content="有效正文" * 450, word_count=1800, review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4(), genre="东方玄幻")

    with pytest.raises(RuntimeError, match="QUALITY_GATE_FAILED"):
        await review(FakeSession(), provider=provider, work=work, run=run)

    assert run.word_count >= 1500
    assert run.review["failure_classes"][0] == "PERFORMANCE"
    assert run.review.get("degraded_pass") is not True


async def test_successful_review_does_not_record_failure_classes() -> None:
    provider = FakeProvider(review_passed=True)
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=1,
        generation_context={
            "recent_chapters": [],
            "director_plan": {
                "state_before": {"location": "车站", "clue": "未知"},
                "state_after": {"location": "旧宅", "clue": "已获得"},
            },
        },
        performances=[], user_guidance={}, title="初稿",
        content="有效正文" * 220, word_count=880, review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4(), genre="东方玄幻")

    await review(FakeSession(), provider=provider, work=work, run=run)

    assert run.review["passed"] is True
    assert "failure_classes" not in run.review


async def test_degraded_pass_cannot_be_used_twice_for_one_chapter() -> None:
    provider = FakeProvider(review_passed=False)
    long_content = "山河日月星辰风雨人物行动因果转折" * 120

    async def prose_provider(*, response_model, **kwargs):
        provider.calls.append(response_model.__name__)
        if response_model is ChapterReview:
            output = ChapterReview(passed=False, prose_issues=["节奏仍显拖沓"])
        elif response_model is ChapterDraft:
            output = ChapterDraft(title="再次修订", content=long_content)
        else:
            return await FakeProvider.generate_structured(
                provider, response_model=response_model, **kwargs
            )
        return StructuredModelResponse(
            output=output,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
            model="fake",
        )

    provider.generate_structured = prose_provider
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=2,
        generation_context={
            "degraded_pass_count": 1,
            "recent_chapters": [],
            "director_plan": {
                "state_before": {"location": "车站", "clue": "未知"},
                "state_after": {"location": "旧宅", "clue": "已获得"},
            },
        },
        performances=[], user_guidance={}, title="初稿",
        content=long_content, word_count=len(long_content), review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4(), genre="东方玄幻")

    with pytest.raises(RuntimeError, match="QUALITY_GATE_FAILED"):
        await review(FakeSession(), provider=provider, work=work, run=run)

    assert run.review.get("degraded_pass") is not True


@pytest.mark.parametrize("issue_field", ["continuity_issues", "leakage_issues"])
@pytest.mark.parametrize("reported_pass", [True, False])
async def test_long_chapter_never_overrides_hard_review_issues(issue_field, reported_pass):
    class InconsistentReviewer:
        async def generate_structured(self, *, response_model, **kwargs):
            if response_model is ChapterReview:
                output = ChapterReview(**{"passed": reported_pass, issue_field: ["明确事实矛盾"]})
            else:
                output = ChapterDraft(title="长稿", content="具体行动与人物选择" * 250)
            return StructuredModelResponse(output=output, usage=ModelUsage(1, 1), model="fake")

    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=1,
        generation_context={"director_plan": {
            "state_before": {"location": "门外", "clue": "无"},
            "state_after": {"location": "室内", "clue": "有"},
        }},
        user_guidance={}, title="长稿", content="具体行动与人物选择" * 250,
        word_count=2250, review={},
    )
    with pytest.raises(RuntimeError, match="QUALITY_GATE_FAILED"):
        await review(FakeSession(), provider=InconsistentReviewer(),
                     work=SimpleNamespace(id=__import__("uuid").uuid4()), run=run)
    assert run.review["passed"] is False
