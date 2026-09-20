"""缺陷 4b：重试必须有间隔，否则重试是把预算一次性烧光的加速器（2026-09-10）。

真实故障：章的 DIRECT 步骤三步失败全是 ``CALL_UNKNOWN``，间隔不到一秒。这个
错误的意思是"钱花没花还不知道，先对账"，而对账静默期是 60 秒；运行一释放
租约，**三个 worker 会在同一瞬间抢到它**——于是"等一分钟就能自愈"的章被
直接判成 TERMINAL_FAILED，自愈条件根本没来得及成立。

修复：可重试的失败不立刻放回场上，而是按住一个比对账静默期更长的租约。
不分错误码：``CallBroker`` 会把任何 provider 异常都转成 CallUnknown
（请求可能已发出，不能假设没花钱），"只对协调类失败后退"写起来是一回事、
跑起来是另一回事。

三条命题，各自能被退回旧行为打红：
1. 可重试失败后租约仍在，且比"现在"更久；
2. 按住期间下一个 worker 领不走（否则重试根本没被推迟）；
3. 后退是"推迟"不是"封死"——租约到期后仍会被重新领取。

ruff: noqa: RUF001
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from regent.novel.application import production, works
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ChapterStepModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select


class UnknownProvider:
    """每次调用都抛 CallUnknown：钱花没花不知道，必须先对账。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, **kwargs):  # noqa: ANN201
        self.calls += 1
        raise production.CallUnknown("must reconcile first")


class BrokenProvider:
    """普通执行类失败：调用确实没成功，重试才是正解。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, **kwargs):  # noqa: ANN201
        self.calls += 1
        raise RuntimeError("provider exploded")


async def _noop_event(session, **kwargs):  # noqa: ANN001, ANN201
    return None


def _naive(value: datetime) -> datetime:
    """SQLite 取回的是无时区时间，比较前先统一。"""
    return value.replace(tzinfo=None)


async def _seed(factory) -> None:  # noqa: ANN001
    owner = uuid.uuid4()
    work_id = uuid.uuid4()
    async with factory() as session:
        session.add(NovelPrincipalModel(id=owner, subject=f"coord:{owner}"))
        session.add(
            StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑")
        )
        session.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        session.add(
            StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价")
        )
        await session.commit()
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()


async def _advance_until_failure(factory, provider) -> None:  # noqa: ANN001
    """推进到某个步骤失败为止。

    ASSEMBLE 不调模型，第一次模型调用发生在 DIRECT——不循环就根本碰不到协调类
    失败，测试会假装在测、其实什么都没测。
    """
    for _ in range(8):
        async with factory() as session:
            progress = await works.advance_background_run(session, provider=provider)
            await session.commit()
        if progress is not None and progress.state.value == "RETRYABLE_FAILED":
            return
    pytest.fail("没能推进到失败步骤")


@pytest.mark.asyncio
async def test_coordination_failure_keeps_the_run_leased(novel_db, monkeypatch):
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = UnknownProvider()
    await _seed(novel_db)
    await _advance_until_failure(novel_db, provider)

    async with novel_db() as session:
        run = (await session.scalars(select(ChapterRunModel))).one()
        assert run.state == "RETRYABLE_FAILED"
        # 租约必须还在，而且要比"现在"更久——否则等于没按住
        assert run.lease_expires_at is not None
        assert _naive(run.lease_expires_at) > _naive(datetime.now(UTC))


@pytest.mark.asyncio
async def test_no_worker_picks_the_run_up_while_it_is_held(novel_db, monkeypatch):
    """按住的意义就是这个：下一个 tick 不得把它领走。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = UnknownProvider()
    await _seed(novel_db)
    await _advance_until_failure(novel_db, provider)
    calls_after_failure = provider.calls

    async with novel_db() as session:
        progress = await works.advance_background_run(session, provider=provider)
        await session.commit()

    assert progress is None, "被按住的运行不该是候选，否则重试根本没被推迟"
    assert provider.calls == calls_after_failure


@pytest.mark.asyncio
async def test_the_hold_is_a_bounded_delay_not_a_dead_end(novel_db, monkeypatch):
    """后退是"推迟"，不是"封死"：有上界，到期后运行重新可被领取。

    不做时间旅行：这条 UNKNOWN 调用在单测里永远等不到对账（没有恢复 tick），
    重试必然被"未对账"挡回，测出来的是环境的局限而不是后退的性质。真正的
    "到期后能重跑"由真实环境跑章验收。
    """
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = UnknownProvider()
    await _seed(novel_db)
    await _advance_until_failure(novel_db, provider)

    async with novel_db() as session:
        run = (await session.scalars(select(ChapterRunModel))).one()

    # 按住期间确实不能领
    assert production.lease_is_free(run) is False
    # 但有上界：不是永久占用
    hold = _naive(run.lease_expires_at) - _naive(datetime.now(UTC))
    assert timedelta(seconds=30) < hold <= timedelta(minutes=10)
    # 到期后重新可领（候选筛选用的就是这个判据）
    assert production.lease_is_free(
        run, now=run.lease_expires_at.replace(tzinfo=UTC) + timedelta(seconds=1)
    )
    # 推迟不等于判死：运行状态仍是可重试
    assert run.state == "RETRYABLE_FAILED"
