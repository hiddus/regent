"""A-02：裁决必须真的改变导演后续创作（Plan v6.9 第一批）。

审计反例：选择只以 option_id 追加进 `decision_resolutions`，WATCH_TAKE /
WATCH_PROSE 的上下文不读它，导演下一轮等于没看见用户的选择；`advance_step`
还对 incomplete 统一写 RUNNING，把 create_decision 写入的 PENDING_DECISION
覆盖掉，于是「作品在等待、章节却在跑」。

验收要求：

- 由导演实际请求启动（不是测试手工塞一条 resolution）；
- 用户选择与到期默认两条路径都走通；
- 下一次模型请求含所选语义，不重复提问，不执行未选分支；
- 竞争与重启只应用一次。
"""

# Chinese fixture prose deliberately uses full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application import works
from regent.novel.domain.states import ChapterRunState, StoryWorkState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    DecisionRequestModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select

from test_decision_loop import OPTIONS  # noqa: E402
from test_direction import Provider, brief, resolution  # noqa: E402

TRUST = "信任建立，风险上升"
REFUSE = "关系僵持，风险推迟"


class _Provider(Provider):
    """记录每次请求的 user_prompt，便于断言导演到底看到了什么。"""

    def __init__(self, outputs):
        super().__init__(outputs)
        self.payloads: list[str] = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.payloads.append(str(kwargs.get("user_prompt", "")))
        return await super().generate_structured(response_model=response_model, **kwargs)


def _plan():
    return d.ChapterDirection(
        title="交付",
        reader_intent="为信任担心",
        ending_reason="交付已成立",
        scenes=[brief()],
    )


def _request(impact: str = "HIGH") -> d.TakeDirection:
    return d.TakeDirection(
        action="CONTINUE",
        observation="走到了需要用户决定的岔路",
        evidence=["他把钥匙放在桌上。"],
        instruction="等用户决定",
        request_decision=d.DecisionRequestSpec(
            trigger_summary="这场戏要不要交出钥匙",
            why_human="影响后续三章的信任线",
            options=[d.DecisionOption(**option) for option in OPTIONS],
            default_option_id="refuse",
            impact_level=impact,
            impact_horizon_chapters=3,
        ),
    )


def _continue():
    return d.TakeDirection(
        action="CONTINUE",
        observation="信任有了具体表现",
        evidence=["他把钥匙放在桌上。"],
        instruction="用停顿表现犹豫",
    )


def _render():
    return d.TakeDirection(
        action="RENDER",
        observation="可以进入呈现了",
        evidence=["他把钥匙放在桌上。"],
        instruction="用停顿表现犹豫",
    )


def _actor():
    return d.ActorTurn(intention="信任", actions=["伸手"], private_reasoning="PRIVATE")


async def _noop_event(session, **kwargs):
    return None


async def _boot(sessions) -> tuple[uuid.UUID, uuid.UUID]:
    owner, work_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as session:
        session.add(NovelPrincipalModel(id=owner, subject=f"a02:{owner}"))
        session.add(
            StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑")
        )
        session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价"))
        session.add(
            PersonaSpecModel(
                id=uuid.uuid4(), work_id=work_id, name="主角", voice={"style": "克制"}
            )
        )
        path_id = uuid.uuid4()
        session.add(CriticalPathModel(id=path_id, work_id=work_id, node_count=1))
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(), path_id=path_id, node_id="n1", ordinal=1, title="交付"
            )
        )
        await session.commit()
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    return owner, work_id


async def _tick(sessions, provider, owner, work_id) -> None:
    async with sessions() as session:
        await works.advance_step(
            session, provider=provider, owner_id=owner, work_id=work_id, chapter_no=1
        )
        await session.commit()


async def _tick_until_decision(sessions, provider, owner, work_id):
    """由导演自己走到请求裁决为止；走不到就是断言失败。"""
    for _ in range(15):
        await _tick(sessions, provider, owner, work_id)
        async with sessions() as session:
            row = await session.scalar(
                select(DecisionRequestModel).where(
                    DecisionRequestModel.state == "PENDING"
                )
            )
            if row is not None:
                return row
    pytest.fail("导演没有请求用户裁决")


async def _run_row(sessions) -> ChapterRunModel:
    async with sessions() as session:
        return await session.scalar(select(ChapterRunModel))


async def _work_row(sessions) -> StoryWorkModel:
    async with sessions() as session:
        return await session.scalar(select(StoryWorkModel))


async def _decision_count(sessions) -> int:
    async with sessions() as session:
        return len((await session.scalars(select(DecisionRequestModel))).all())


async def test_director_receives_full_semantics_of_user_choice(novel_db, monkeypatch):
    """用户选择：下一次导演请求必须含所选后果，且不含未选分支。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider([_plan(), _actor(), resolution(), _request()])
    owner, work_id = await _boot(novel_db)

    pending = await _tick_until_decision(novel_db, provider, owner, work_id)
    run = await _run_row(novel_db)
    assert run.state == ChapterRunState.PENDING_DECISION.value, (
        f"章节应停在等待裁决，实际 {run.state}"
    )
    work = await _work_row(novel_db)
    assert work.state == StoryWorkState.PENDING_DECISION.value

    async with novel_db() as session:
        await works.resolve_decision(
            session,
            owner_id=owner,
            work_id=work_id,
            decision_id=pending.id,
            option_id="trust",
            accept_default=False,
            confirm_nonce=pending.confirm_nonce,
        )
        await session.commit()

    run = await _run_row(novel_db)
    assert run.state == ChapterRunState.RUNNING.value, "裁决后章节未恢复推进"
    stored = run.generation_context["decision_resolutions"][-1]
    assert stored["option_id"] == "trust"
    assert stored["near_term_consequence"] == TRUST, "选择只存了 id，没存创作语义"
    assert stored["reversibility"] == "COSTLY"
    assert stored["consumed"] is False, "还没被导演执行的裁决不能标记为已消费"

    # 下一次导演请求：必须带着所选语义继续
    provider.outputs.insert(0, _continue())
    await _tick(novel_db, provider, owner, work_id)

    last = provider.payloads[-1]
    assert TRUST in last, f"导演请求里没有用户所选的后果：{last[-400:]}"
    assert "交出钥匙" in last, "所选选项的标签没有进入导演请求"
    assert REFUSE not in last, "未选分支进入了导演请求"

    run = await _run_row(novel_db)
    stored = run.generation_context["decision_resolutions"][-1]
    assert stored["consumed"] is True, "裁决没有被导演消费"
    assert "pending_decision" not in run.generation_context["production"]

    # 再走回 WATCH_TAKE：不得重复提问，也不得再次注入同一裁决
    before = await _decision_count(novel_db)
    provider.outputs.insert(0, _actor())
    await _tick(novel_db, provider, owner, work_id)
    provider.outputs.insert(0, resolution())
    await _tick(novel_db, provider, owner, work_id)
    provider.outputs.insert(0, _render())
    await _tick(novel_db, provider, owner, work_id)
    assert await _decision_count(novel_db) == before, "导演就同一件事重复发起了裁决"
    assert TRUST not in provider.payloads[-1], "裁决被重复注入后续请求"


async def test_default_option_reaches_director_after_deadline(novel_db, monkeypatch):
    """到期默认：默认项的语义同样必须进入导演请求。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider([_plan(), _actor(), resolution(), _request("MEDIUM")])
    owner, work_id = await _boot(novel_db)

    await _tick_until_decision(novel_db, provider, owner, work_id)
    run = await _run_row(novel_db)
    assert run.state == ChapterRunState.PENDING_DECISION.value

    async with novel_db() as session:
        swept = await works.sweep_expired_decisions(
            session, now=datetime.now(UTC) + timedelta(hours=25)
        )
        await session.commit()
    assert len(swept) == 1, f"到期裁决没有被默认项落定：{swept}"

    run = await _run_row(novel_db)
    assert run.state == ChapterRunState.RUNNING.value
    stored = run.generation_context["decision_resolutions"][-1]
    assert stored["option_id"] == "refuse"
    assert stored["near_term_consequence"] == REFUSE
    assert stored["resolved_by"] == "timer"

    provider.outputs.insert(0, _continue())
    await _tick(novel_db, provider, owner, work_id)
    last = provider.payloads[-1]
    assert REFUSE in last, f"默认项没有进入导演请求：{last[-400:]}"
    assert TRUST not in last, "未选分支进入了导演请求"

    run = await _run_row(novel_db)
    assert run.generation_context["decision_resolutions"][-1]["consumed"] is True


async def test_decision_is_applied_once_under_restart(novel_db, monkeypatch):
    """重启后重复推进：裁决只被消费一次，不会二次改变后续创作。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider([_plan(), _actor(), resolution(), _request()])
    owner, work_id = await _boot(novel_db)

    pending = await _tick_until_decision(novel_db, provider, owner, work_id)
    async with novel_db() as session:
        await works.resolve_decision(
            session,
            owner_id=owner,
            work_id=work_id,
            decision_id=pending.id,
            option_id="trust",
            accept_default=False,
            confirm_nonce=pending.confirm_nonce,
        )
        await session.commit()
    # 第二次提交必须失败：裁决已经落定，不能改写历史
    async with novel_db() as session:
        from regent.novel.domain.errors import Conflict

        with pytest.raises(Conflict):
            await works.resolve_decision(
                session,
                owner_id=owner,
                work_id=work_id,
                decision_id=pending.id,
                option_id="refuse",
                accept_default=False,
                confirm_nonce=pending.confirm_nonce,
            )

    provider.outputs.insert(0, _continue())
    await _tick(novel_db, provider, owner, work_id)
    first_with_decision = [
        index for index, text in enumerate(provider.payloads) if TRUST in text
    ]
    assert first_with_decision == [len(provider.payloads) - 1], (
        f"裁决被注入了多次：{first_with_decision}"
    )

    run = await _run_row(novel_db)
    resolutions = run.generation_context["decision_resolutions"]
    assert len(resolutions) == 1, "同一次裁决被记录了多条"
    assert resolutions[0]["option_id"] == "trust"
