from types import SimpleNamespace

from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application.generation import (
    ChapterDraft,
    ChapterReview,
    DirectorPlan,
    _visible_performances,
    direct,
    review,
    weave,
)


class FakeProvider:
    def __init__(self, *, review_passed: bool = True) -> None:
        self.review_passed = review_passed
        self.calls: list[str] = []

    async def generate_structured(self, *, response_model, **_):
        self.calls.append(response_model.__name__)
        if response_model is DirectorPlan:
            output = DirectorPlan(
                scene_goal="主角必须拿到线索",
                beats=["遇阻", "试探", "付出代价"],
                ending_hook="旧表开始倒走",
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
            output = ChapterDraft(title=marker, content=marker + "。" * 700)
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

    def add(self, row) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        return None


def test_private_reasoning_never_reaches_director_or_weaver() -> None:
    run = SimpleNamespace(
        performances=[{
            "persona": "同伴", "private_reasoning": "我要隐瞒钥匙",
            "actions": ["收起手"], "dialogue": ["没什么"],
        }]
    )
    visible = _visible_performances(run)
    assert "private_reasoning" not in visible[0]
    assert visible[0]["actions"] == ["收起手"]


async def test_agent_loop_directs_then_weaves_readable_chapter() -> None:
    provider = FakeProvider()
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=1,
        generation_context={"goal": "找到父亲"},
        performances=[{"persona": "主角", "actions": ["追查旧表"]}],
        title="",
        content="",
        word_count=0,
        review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4())
    session = FakeSession()
    await direct(session, provider=provider, work=work, run=run)
    await weave(session, provider=provider, work=work, run=run)
    assert run.generation_context["director_plan"]["ending_hook"] == "旧表开始倒走"
    assert run.word_count >= 600
    assert provider.calls == ["DirectorPlan", "ChapterDraft"]


async def test_failed_review_triggers_evidence_based_revision() -> None:
    provider = FakeProvider(review_passed=False)
    run = SimpleNamespace(
        id=__import__("uuid").uuid4(), chapter_no=1,
        generation_context={"goal": "找到父亲"},
        performances=[],
        title="初稿",
        content="初稿。" * 300,
        word_count=900,
        review={},
    )
    work = SimpleNamespace(id=__import__("uuid").uuid4())
    await review(FakeSession(), provider=provider, work=work, run=run)
    assert run.title == "修订稿"
    assert run.review["passed"] is True
    assert provider.calls == ["ChapterReview", "ChapterDraft", "ChapterReview"]
