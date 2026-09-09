"""恢复与账本缺口的行为测试（Plan v6.4 §10 P0-1 ~ P0-3）。

这四个用例对应 2026-09-07 SQLite 探测复现的四个缺陷，先写成会失败的复现，
再逐个修。金额是测试中的整数单位，不是真实供应商账单。

- D1 默认配置连续三次 reconcile 均为 PENDING（对账次数被当成模型 attempt）
- D2 供应商查询成功后 reservation 仍为 RESERVED 且无费用记录
- D3 两次 attempt 的调用金额合计与消费账不一致（幂等键缺 attempt 维度）
- D4 预算仅剩 1 单位时仍发起调用，累计预留突破上限
"""

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
    def __init__(self, outputs, *, fail: Exception | None = None, lookup=None):
        self.outputs = list(outputs)
        self.requests: list[dict] = []
        self._fail = fail
        self._lookup = lookup

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append(kwargs)
        if self._fail is not None:
            raise self._fail
        output = self.outputs.pop(0)
        return StructuredModelResponse(
            output=output, usage=ModelUsage(1000, 500, cached_input_tokens=200), model="test"
        )

    async def lookup_call(self, request_id: str):
        return self._lookup(request_id) if self._lookup else None


async def work_row(session):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"recovery-test:{owner}"))
    work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑")
    session.add(work)
    await session.flush()
    return work


def broker(**kwargs):
    return production.CallBroker(lease_owner="worker:1", **kwargs)


async def _sums(session):
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
    return int(consumed or 0), int(released or 0)


async def _calls(session):
    return (
        await session.scalars(
            select(ModelCallModel).order_by(ModelCallModel.attempt)
        )
    ).all()


# ---------------------------------------------------------------------------
# D1：默认配置必须能有界结束
# ---------------------------------------------------------------------------


async def test_d1_default_config_reconciliation_terminates(novel_db):
    """默认配置下连续对账必须有界结束，不能永远 PENDING。"""
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

    states = []
    for _ in range(10):
        async with novel_db() as session:
            state = await broker().reconcile(session, logical_call_id=logical_call_id)
        states.append(state)
        if state != "PENDING":
            break
    # 默认 reconcile_attempts=3：至多三次必须给出终态，不得永远挂起
    assert states[-1] == "FAILED", f"对账无界挂起：{states}"
    assert len(states) <= production.DEFAULT_RECONCILE_ATTEMPTS, f"终止过慢：{states}"


# ---------------------------------------------------------------------------
# D2：供应商查询成功后必须结清预留并留下费用记录
# ---------------------------------------------------------------------------


async def test_d2_provider_lookup_settles_the_reservation(novel_db):
    provider = Provider(
        [],
        fail=TimeoutError("gateway timeout"),
        lookup=lambda rid: {
            "output": {"text": "供应商侧拿到了输出"},
            "input_tokens": 1000,
            "output_tokens": 500,
        },
    )
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
        call.provider_request_id = "req-1"
        await session.flush()
        await session.commit()
        logical_call_id = call.logical_call_id

    async with novel_db() as session:
        state = await broker().reconcile(
            session, logical_call_id=logical_call_id, provider=provider
        )
        assert state == "SUCCEEDED"
        call = await session.scalar(select(ModelCallModel))
        reservation = await session.scalar(select(QuotaReservationModel))
        consumed, released = await _sums(session)
        # 查询成功 ≠ 悬空预留：必须留下费用记录并结清预留
        assert consumed > 0, "供应商查询成功后没有任何费用记录"
        assert reservation.status == "SETTLED", "预留仍悬空"
        assert reservation.amount_minor == consumed + released
        assert call.actual_amount_minor == consumed
        assert call.output_json == {"text": "供应商侧拿到了输出"}


# ---------------------------------------------------------------------------
# D3：每次尝试的费用都必须独立进账
# ---------------------------------------------------------------------------


async def test_d3_each_attempt_is_settled_independently(novel_db):
    """第一次尝试挂账后按预留额结算，第二次尝试成功——两笔都要在账上。"""
    failing = Provider([], fail=TimeoutError("gateway timeout"))
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        with pytest.raises(production.CallUnknown):
            await broker().run(
                session,
                provider=failing,
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
        await broker(reconcile_attempts=1).reconcile(
            session, logical_call_id=logical_call_id
        )

    succeeding = Provider([Echo(text="第二次尝试")])
    async with novel_db() as session:
        work = await work_row(session)
        result = await broker().run(
            session,
            provider=succeeding,
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
        assert result.attempt == 2
        calls = await _calls(session)
        consumed, released = await _sums(session)
        # 账本必须与每次尝试记录的金额一致：不能因为同键而漏记第二次
        billed = sum(int(c.actual_amount_minor or 0) for c in calls if c.actual_amount_minor)
        assert consumed == billed, f"消费账 {consumed} 与调用金额 {billed} 不一致"
        reserved_total = await session.scalar(
            select(func.sum(QuotaReservationModel.amount_minor))
        )
        assert int(reserved_total) == consumed + released


# ---------------------------------------------------------------------------
# D4：预算仅剩极少额度时必须在发出调用前拒绝
# ---------------------------------------------------------------------------


async def test_d4_budget_is_checked_against_outstanding_not_cumulative(novel_db):
    """已消费接近上限时，下一次调用必须在发出前被拒绝，不得突破上限。"""
    from types import SimpleNamespace

    from regent.novel.application import direction as d
    from regent.novel.domain.price_book import estimate_minor

    estimate = estimate_minor("test", prompt_chars=4000, system_chars=500)
    assert estimate > 1
    headroom = d.MAX_COST_MINOR - estimate + 1  # 只剩 1 单位可用

    provider = Provider([Echo(text="不该发出")])
    run = SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        input_version=1,
        current_step="PRODUCE",
        generation_context={},
    )
    work = SimpleNamespace(id=uuid.uuid4())
    production_state = {"committed_minor": headroom}

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
            {"payload": "x" * 4000},
            "plan",
            "v1:plan",
        )
    assert not provider.requests, "预算不足仍发起了调用"


async def test_d4_reservation_never_exceeds_the_chapter_ceiling(novel_db):
    """并发预留不得突破同一上限：预留本身带原子上限检查。"""
    from regent.novel.application import ledger

    async with novel_db() as session:
        work = await work_row(session)
        await ledger.reserve(
            session,
            reservation_key="k1",
            work_id=work.id,
            amount_minor=8,
            chapter_no=1,
            funding_limit_minor=10,
        )
        with pytest.raises(ledger.QuotaExceeded):
            await ledger.reserve(
                session,
                reservation_key="k2",
                work_id=work.id,
                amount_minor=8,
                chapter_no=1,
                funding_limit_minor=10,
            )
        # 另一章的额度独立核算，不应被第一章占满
        await ledger.reserve(
            session,
            reservation_key="k3",
            work_id=work.id,
            amount_minor=8,
            chapter_no=2,
            funding_limit_minor=10,
        )


async def test_d4_unknown_call_cost_is_never_dropped(novel_db):
    """已发生的真实费用不能因为超限而丢账。"""
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
        reserved = int(call.reserved_amount_minor)
        logical_call_id = call.logical_call_id

    async with novel_db() as session:
        await broker(reconcile_attempts=1).reconcile(
            session, logical_call_id=logical_call_id
        )
        consumed, _ = await _sums(session)
        assert consumed == reserved, "放弃对账后已发生费用被丢账"


# ---------------------------------------------------------------------------
# P0-1：worker 重启后的自动恢复清扫
# ---------------------------------------------------------------------------


async def _crashed_call(session, run_id, *, command_id="v1:plan", request_id=""):
    """模拟进程在模型调用中崩溃：留下 RESERVED 记录且租约已过期。"""
    key = production.logical_call_key(run_id, command_id, "", "plan")
    b = broker(lease_ttl=timedelta(seconds=1))
    async with b._tx(session) as acc:
        await b._prepare(
            acc,
            logical_call_id=key,
            work_id=(await work_row(session)).id,
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
    call = await session.scalar(select(ModelCallModel))
    call.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if request_id:
        call.provider_request_id = request_id
    await session.flush()
    await session.commit()
    return key


async def test_p01_restart_recovery_reclaims_then_settles(novel_db):
    """重启后：过期 RESERVED 先回收为 UNKNOWN，再按对账次数有界结算，不留悬账。"""
    run_id = uuid.uuid4()
    async with novel_db() as session:
        key = await _crashed_call(session, run_id)

    async with novel_db() as session:
        stats = await production.recover_novel_calls(
            session, grace=timedelta(0), reconcile_attempts=1
        )
        assert stats["reclaimed"] == 1, stats
        assert stats["failed"] == 1, stats
        call = await session.scalar(
            select(ModelCallModel).where(ModelCallModel.logical_call_id == key)
        )
        assert call.status == production.CALL_STATUS_FAILED
        assert call.error_code == "RECONCILE_EXHAUSTED"
        reservation = await session.scalar(select(QuotaReservationModel))
        assert reservation.status == "SETTLED", "重启恢复后仍有悬空预留"
        consumed, released = await _sums(session)
        assert consumed == int(call.reserved_amount_minor)
        assert reservation.amount_minor == consumed + released


async def test_p01_recovery_reuses_provider_result_without_new_call(novel_db):
    """对账查到结果就据实结算，之后同键重跑直接复用、不再发起新调用。"""
    provider = Provider(
        [],
        fail=TimeoutError("gateway timeout"),
        lookup=lambda rid: {
            "output": {"text": "供应商侧补回的输出"},
            "input_tokens": 1000,
            "output_tokens": 500,
        },
    )
    run_id = uuid.uuid4()
    async with novel_db() as session:
        await _crashed_call(session, run_id, request_id="req-1")

    async with novel_db() as session:
        stats = await production.recover_novel_calls(
            session, provider=provider, grace=timedelta(0)
        )
        assert stats["succeeded"] == 1, stats

    fresh = Provider([Echo(text="不该被使用")])
    async with novel_db() as session:
        await work_row(session)
        result = await broker().run(
            session,
            provider=fresh,
            schema=Echo,
            work_id=uuid.uuid4(),
            run_id=run_id,
            chapter_no=1,
            step="PRODUCE",
            purpose="plan",
            command_id="v1:plan",
            system_prompt="sys",
            user_prompt="user",
        )
        assert result.reused is True
        assert result.output.text == "供应商侧补回的输出"
        assert not fresh.requests, "已有成功结果仍重新调用了模型"


async def test_p01_recovery_does_not_touch_calls_inside_grace(novel_db):
    """静默期内的 UNKNOWN 不得被提前按“钱已花掉”结清。"""
    provider = Provider([], fail=TimeoutError("gateway timeout"))
    async with novel_db() as session:
        await work_row(session)
        with pytest.raises(production.CallUnknown):
            await broker().run(
                session,
                provider=provider,
                schema=Echo,
                work_id=uuid.uuid4(),
                run_id=uuid.uuid4(),
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                command_id="v1:plan",
                system_prompt="sys",
                user_prompt="user",
            )
        await session.commit()

    async with novel_db() as session:
        stats = await production.recover_novel_calls(session, grace=timedelta(hours=1))
        assert stats == {
            "reclaimed": 0,
            "succeeded": 0,
            "failed": 0,
            "pending": 0,
            "skipped": 0,
        }, stats
        call = await session.scalar(select(ModelCallModel))
        assert call.status == production.CALL_STATUS_UNKNOWN
        consumed, _ = await _sums(session)
        assert consumed == 0, "静默期内被提前结算"


async def test_p01_failure_keeps_provider_request_id_for_lookup(novel_db):
    """超时异常若带供应商 request_id，必须落到调用记录上，否则对账无可查。"""

    class TimeoutWithRequestId(TimeoutError):
        request_id = "req-timeout-1"

    provider = Provider([], fail=TimeoutWithRequestId("gateway timeout"))
    async with novel_db() as session:
        await work_row(session)
        with pytest.raises(production.CallUnknown):
            await broker().run(
                session,
                provider=provider,
                schema=Echo,
                work_id=uuid.uuid4(),
                run_id=uuid.uuid4(),
                chapter_no=1,
                step="PRODUCE",
                purpose="plan",
                command_id="v1:plan",
                system_prompt="sys",
                user_prompt="user",
            )
        call = await session.scalar(select(ModelCallModel))
        assert call.provider_request_id == "req-timeout-1"


async def test_p01_worker_tick_delegates_to_recovery(novel_db):
    """worker 的恢复 tick 必须真的接到恢复清扫上，而不是空转。"""
    from regent.worker.main import Worker

    run_id = uuid.uuid4()
    async with novel_db() as session:
        await _crashed_call(session, run_id)

    worker = Worker(
        worker_id="test-worker",
        dispatcher=None,
        leases=None,
        sessions=novel_db,
        poll_seconds=0.1,
        heartbeat_seconds=1.0,
    )
    # 静默期较长时不结清；这里只验证调用链贯通：崩溃留下的 RESERVED 被回收成 UNKNOWN
    await worker._novel_recovery_tick(startup=True)
    async with novel_db() as session:
        call = await session.scalar(select(ModelCallModel))
        assert call.status == production.CALL_STATUS_UNKNOWN


async def test_lease_expiry_is_recorded_with_audit_time(novel_db):
    """租约过期转 UNKNOWN 时保留可审计时间。"""
    provider = Provider([Echo(text="x")])
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        key = production.logical_call_key(run_id, "cmd", "", "plan")
        b = broker(lease_ttl=timedelta(seconds=1))
        async with b._tx(session) as acc:
            await b._prepare(
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
        call = await session.scalar(select(ModelCallModel))
        call.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()
        assert await production.reclaim_expired_calls(session) == [key]
        call = await session.scalar(select(ModelCallModel))
        assert call.status == production.CALL_STATUS_UNKNOWN
        assert call.updated_at is not None
        assert not provider.requests
