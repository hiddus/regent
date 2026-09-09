"""生产调用协议行为测试（Tech-Spec §4.4 / §5 / §6 / G-09 / G-10）。

覆盖：调用前预留、调用后结算、恢复复用不重复计费、同键异参冲突、
外部结果不确定时挂账且不盲重试、货币预算上限独立于调用次数上限。
"""

# ruff: noqa: RUF001

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, Field
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import production
from regent.novel.infrastructure.models import (
    CostEntryModel,
    ModelCallModel,
    NovelPrincipalModel,
    QuotaReservationModel,
    StoryWorkModel,
)
from sqlalchemy import func, select


class Echo(BaseModel):
    text: str = Field(min_length=1)


class Provider:
    def __init__(self, outputs, *, fail: Exception | None = None):
        self.outputs = list(outputs)
        self.requests: list[dict] = []
        self._fail = fail

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append(kwargs)
        if self._fail is not None:
            raise self._fail
        output = self.outputs.pop(0)
        return StructuredModelResponse(
            output=output, usage=ModelUsage(1000, 500, cached_input_tokens=200), model="test"
        )


async def work_row(session):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"production-test:{owner}"))
    work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑")
    session.add(work)
    await session.flush()
    return work


def broker(**kwargs):
    return production.CallBroker(lease_owner="worker:1", **kwargs)


async def test_reserve_before_call_then_settle_after(novel_db):
    provider = Provider([Echo(text="第一版")])
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        result = await broker().run(
            session,
            provider=provider,
            schema=Echo,
            work_id=work.id,
            run_id=run_id,
            chapter_no=1,
            step="PRODUCE",
            purpose="plan",
            command_id="v1:plan",
            system_prompt="sys",
            user_prompt="user",
        )
        assert result.output.text == "第一版"
        assert result.reserved_minor > 0 and result.actual_minor > 0
        assert not result.reused
        call = await session.scalar(select(ModelCallModel))
        assert call.status == production.CALL_STATUS_SUCCEEDED
        assert call.usage_source == "provider"
        assert call.output_json == {"text": "第一版"}
        assert call.price_book_version
        reservation = await session.scalar(select(QuotaReservationModel))
        consumed = await session.scalar(
            select(func.sum(CostEntryModel.amount_minor)).where(
                CostEntryModel.entry_kind == "CONSUME"
            )
        )
        released = await session.scalar(
            select(func.sum(CostEntryModel.amount_minor)).where(
                CostEntryModel.entry_kind == "RELEASE"
            )
        )
        # 账本恒等式：预留 = 已结算 + 已释放
        assert reservation.amount_minor == consumed + released
        assert call.actual_amount_minor == consumed


async def test_recovery_reuses_successful_logical_call_without_new_charge(novel_db):
    """崩溃恢复后重发同一命令：不再调用模型，也不再计费（G-09）。"""
    provider = Provider([Echo(text="唯一的输出")])
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        first = await broker().run(
            session,
            provider=provider,
            schema=Echo,
            work_id=work.id,
            run_id=run_id,
            chapter_no=1,
            step="PRODUCE",
            purpose="plan",
            command_id="v1:plan",
            system_prompt="sys",
            user_prompt="user",
        )
    async with novel_db() as session:
        work = await work_row(session)
        second = await broker().run(
            session,
            provider=provider,
            schema=Echo,
            work_id=work.id,
            run_id=run_id,
            chapter_no=1,
            step="PRODUCE",
            purpose="plan",
            command_id="v1:plan",
            system_prompt="sys",
            user_prompt="user",
        )
    assert len(provider.requests) == 1
    assert second.reused and second.output.text == "唯一的输出"
    assert second.avoided_minor == first.actual_minor
    assert second.actual_minor == first.actual_minor


async def test_same_key_different_input_is_conflict_not_reuse(novel_db):
    provider = Provider([Echo(text="a"), Echo(text="b")])
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        await broker().run(
            session,
            provider=provider,
            schema=Echo,
            work_id=work.id,
            run_id=run_id,
            chapter_no=1,
            step="PRODUCE",
            purpose="plan",
            command_id="v1:plan",
            system_prompt="sys",
            user_prompt="原始输入",
        )
        with pytest.raises(production.CallConflict):
            await broker().run(
                session,
                provider=provider,
                schema=Echo,
                work_id=work.id,
                run_id=run_id,
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                command_id="v1:plan",
                system_prompt="sys",
                user_prompt="改过的输入",
            )
    assert len(provider.requests) == 1


async def test_unknown_result_is_held_and_never_blind_retried(novel_db):
    """调用中断 → 挂账 UNKNOWN，预留不释放；再次请求同一命令不得盲重试。"""
    provider = Provider([], fail=TimeoutError("gateway timeout"))
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        with pytest.raises(production.CallUnknown):
            await broker().run(
                session,
                provider=provider,
                schema=Echo,
                work_id=work.id,
                run_id=run_id,
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                command_id="v1:plan",
                system_prompt="sys",
                user_prompt="user",
            )
        call = await session.scalar(select(ModelCallModel))
        assert call.status == production.CALL_STATUS_UNKNOWN
        assert call.reserved_amount_minor and call.reserved_amount_minor > 0
        # 未对账前不得产生 CONSUME/RELEASE 流水：钱可能已经花掉
        assert (
            await session.scalar(
                select(func.count()).select_from(CostEntryModel)
            )
        ) == 0

    async with novel_db() as session:
        work = await work_row(session)
        with pytest.raises(production.CallUnknown):
            await broker().run(
                session,
                provider=provider,
                schema=Echo,
                work_id=work.id,
                run_id=run_id,
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                command_id="v1:plan",
                system_prompt="sys",
                user_prompt="user",
            )
    assert len(provider.requests) == 1  # 没有盲重试


async def test_reconcile_settles_unknown_after_attempts_are_exhausted(novel_db):
    provider = Provider([], fail=TimeoutError("gateway timeout"))
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        with pytest.raises(production.CallUnknown):
            await broker().run(
                session,
                provider=provider,
                schema=Echo,
                work_id=work.id,
                run_id=run_id,
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                command_id="v1:plan",
                system_prompt="sys",
                user_prompt="user",
            )
        logical_call_id = (await session.scalar(select(ModelCallModel))).logical_call_id

    async with novel_db() as session:
        state = await broker(reconcile_attempts=1).reconcile(
            session, logical_call_id=logical_call_id
        )
        assert state == "FAILED"
        call = await session.scalar(select(ModelCallModel))
        assert call.status == production.CALL_STATUS_FAILED
        assert call.usage_source == "estimated"
        # 放弃对账后按“费用已发生”结算，不留悬账
        consumed = await session.scalar(
            select(func.sum(CostEntryModel.amount_minor)).where(
                CostEntryModel.entry_kind == "CONSUME"
            )
        )
        assert consumed == call.reserved_amount_minor


async def test_expired_lease_becomes_unknown_instead_of_second_call(novel_db):
    provider = Provider([Echo(text="x")])
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        # 预留后不结算，模拟进程崩溃
        key = production.logical_call_key(run_id, "cmd", "", "plan")
        b = broker(lease_ttl=timedelta(seconds=1))
        async with b._tx(session) as acc:
            ticket = await b._prepare(
                acc,
                logical_call_id=key,
                work_id=work.id,
                run_id=run_id,
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                prompt_hash=production._sha("sys"),
                context_hash=production._sha("user"),
                model_hint="test",
                prompt_chars=10,
                system_chars=10,
            )
        assert ticket is not None
        await session.commit()
        # 手动让租约过期
        call = await session.scalar(select(ModelCallModel))
        call.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()
        reaped = await production.reclaim_expired_calls(session)
        assert reaped == [key]
        await session.commit()

    async with novel_db() as session:
        work = await work_row(session)
        with pytest.raises(production.CallUnknown):
            await broker().run(
                session,
                provider=provider,
                schema=Echo,
                work_id=work.id,
                run_id=run_id,
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                command_id="cmd",
                system_prompt="sys",
                user_prompt="user",
            )
    assert not provider.requests


async def test_currency_budget_stops_production_before_another_call(novel_db):
    """货币预算上限与调用次数上限互不替代（§4.3）。"""
    from types import SimpleNamespace

    from regent.novel.application import direction as d
    provider = Provider([Echo(text="不会用到")])
    run = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_no=1,
        input_version=1,
        current_step="PRODUCE",
        generation_context={},
    )
    work = SimpleNamespace(id=uuid.uuid4())
    # 预算按已结算金额判断：预留会被释放，累计预留不是已花掉的钱。
    production_state = {"committed_minor": d.MAX_COST_MINOR}

    class _Session:
        async def commit(self):
            return None

    with pytest.raises(d.ProductionStopped, match="货币预算"):
        await d._call(
            _Session(),
            provider,
            work,
            run,
            production_state,
            Echo,
            "sys",
            {"payload": 1},
            "plan",
            "v1:plan",
        )
    assert not provider.requests


async def test_run_lease_blocks_parallel_worker(novel_db):
    from regent.novel.infrastructure.models import ChapterRunModel

    async with novel_db() as session:
        work = await work_row(session)
        run = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=uuid.uuid4(),
            chapter_no=1,
            attempt=1,
            state="RUNNING",
        )
        session.add(run)
        await session.flush()
        token = await production.acquire_run_lease(session, run=run, owner="worker:1")
        assert token == 1
        with pytest.raises(production.CallInFlight):
            await production.acquire_run_lease(session, run=run, owner="worker:2")
        await production.release_run_lease(session, run=run, owner="worker:1")
        assert run.lease_owner is None
        # 租约释放后他人可以领取
        assert await production.acquire_run_lease(session, run=run, owner="worker:2") == 2
