"""A-01：补账必须带 attempt 幂等维度（Plan v6.9 第一批）。

审计反例：预留键含 attempt（``{id}:{attempt}:topup``）而消费键不含
（``{id}:topup``）时，两个 attempt 分别补记 3 和 4，消费账只有 3，第二笔
预留停在 RESERVED。钱真花出去了，账上只记一次——这是**漏账**，不是精度问题。

验收要求（来自审计 A-01）：
- 连续两个 attempt 均超预留，覆盖正常返回、供应商查回和 UNKNOWN 重试；
- 实际费用等于消费账，每笔 reservation 结清；
- 重复恢复不重记。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import ledger, production
from regent.novel.application.production import CallBroker
from regent.novel.domain.errors import GuardViolation
from regent.novel.infrastructure.models import (
    CostEntryModel,
    ModelCallModel,
    NovelPrincipalModel,
    QuotaReservationModel,
    StoryWorkModel,
)
from sqlalchemy import func, select

from test_recovery_ledger import Echo, Provider, _sums, work_row  # noqa: E402


def _broker(**kwargs) -> CallBroker:
    return CallBroker(lease_owner="worker:topup", **kwargs)


def _fake_call(work_id: uuid.UUID, attempt: int, logical_call_id: str = "same-call"):
    return SimpleNamespace(
        logical_call_id=logical_call_id,
        attempt=attempt,
        work_id=work_id,
        chapter_no=1,
        step="PRODUCE",
    )


async def test_topup_of_two_attempts_is_booked_separately(novel_db):
    """审计反例本身：3 + 4 必须等于 7，且两笔预留都结清。"""
    async with novel_db() as session:
        work = await work_row(session)
        broker = _broker()
        await broker._top_up(session, call=_fake_call(work.id, 1), amount=3)
        await broker._top_up(session, call=_fake_call(work.id, 2), amount=4)
        await session.commit()
        consumed, _released = await _sums(session)
        rows = (await session.scalars(select(QuotaReservationModel))).all()

    assert consumed == 7, f"补账漏记：应为 7，实际 {consumed}"
    assert len(rows) == 2
    assert all(r.status == "SETTLED" for r in rows), [
        (r.reservation_key, r.amount_minor, r.settled_minor, r.status) for r in rows
    ]


async def test_repeated_recovery_is_not_double_booked(novel_db):
    """重复恢复不重记：同一 attempt 的补账重复执行只记一次。"""
    async with novel_db() as session:
        work = await work_row(session)
        broker = _broker()
        for _ in range(3):
            await broker._top_up(session, call=_fake_call(work.id, 1), amount=5)
        await session.commit()
        consumed, _released = await _sums(session)
    assert consumed == 5, f"重复恢复重复记账：应为 5，实际 {consumed}"


async def test_same_key_different_amount_is_refused(novel_db):
    """同键异额说明键拼错了：静默返回旧流水会让差额消失，必须失败。"""
    async with novel_db() as session:
        work = await work_row(session)
        await ledger.reserve(session, reservation_key="k", work_id=work.id,
                             amount_minor=10, chapter_no=1)
        await ledger.consume(session, reservation_key="k", amount_minor=4,
                             work_id=work.id, logical_call_id="same:1")
        with pytest.raises(GuardViolation):
            await ledger.consume(session, reservation_key="k", amount_minor=6,
                                 work_id=work.id, logical_call_id="same:1")


class _Expensive:
    """返回超大用量的 provider：保证实际费用远超预留，必然触发补账。"""

    def __init__(self, *, fail_first: bool = False, lookup: dict | None = None):
        self.calls = 0
        self._fail_first = fail_first
        self._lookup = lookup
        self.requests: list[dict] = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        if self._fail_first and self.calls == 1:
            error = TimeoutError("gateway timeout")
            error.request_id = "req-topup-1"  # 供应商可查回的前提
            raise error
        return StructuredModelResponse(
            output=Echo(text="ok"),
            usage=ModelUsage(1_000_000, 1_000_000),
            model="test",
        )

    async def lookup_call(self, request_id: str):
        return self._lookup


async def test_normal_return_over_reservation_is_fully_booked(novel_db):
    """正常返回路径：实际费用超出预留的部分必须全部进账。"""
    provider = _Expensive()
    async with novel_db() as session:
        work = await work_row(session)
        result = await _broker().run(
            session,
            provider=provider,
            schema=Echo,
            work_id=work.id,
            run_id=uuid.uuid4(),
            chapter_no=1,
            step="PRODUCE",
            purpose="plan",
            command_id="v1:plan",
            system_prompt="sys",
            user_prompt="user",
        )
        await session.commit()
        consumed, released = await _sums(session)
        reservations = (await session.scalars(select(QuotaReservationModel))).all()
        call = (await session.scalars(select(ModelCallModel))).one()

    assert result.actual_minor > result.reserved_minor, "构造前提：实际费用超预留"
    assert consumed == result.actual_minor, f"账实不符：账 {consumed} / 实 {result.actual_minor}"
    assert call.actual_amount_minor == result.actual_minor
    assert all(r.status == "SETTLED" for r in reservations), [
        (r.reservation_key, r.status) for r in reservations
    ]
    assert released >= 0


async def test_unknown_retry_and_provider_lookup_are_both_booked(novel_db):
    """UNKNOWN 重试 + 供应商查回：两次超预留都要记账，不能互相吞掉。"""
    run_id = uuid.uuid4()
    calls = {"n": 0}

    provider = _Expensive(
        fail_first=True,
        lookup={"input_tokens": 1_000_000, "output_tokens": 1_000_000, "output": None},
    )

    async with novel_db() as session:
        work = await work_row(session)
        with pytest.raises(production.CallUnknown):
            await _broker().run(
                session, provider=provider, schema=Echo, work_id=work.id,
                run_id=run_id, chapter_no=1, step="PRODUCE", purpose="plan",
                command_id="v1:plan", system_prompt="sys", user_prompt="user",
            )
        await session.commit()

    # 供应商查回：按实际用量结清（含超预留补账）
    async with novel_db() as session:
        logical_call_id = (
            await session.scalar(select(ModelCallModel))
        ).logical_call_id
        outcome = await _broker().reconcile(
            session, logical_call_id=logical_call_id, provider=provider
        )
        await session.commit()
        after_lookup, _ = await _sums(session)
        first_call = (
            await session.scalars(
                select(ModelCallModel).where(ModelCallModel.attempt == 1)
            )
        ).one()
    assert outcome == "FAILED"  # 查到用量但没有输出 → 失败但仍要结清
    assert after_lookup == first_call.actual_amount_minor, (
        f"查回后账实不符：账 {after_lookup} / 实 {first_call.actual_amount_minor}"
    )

    # 重试：attempt=2 同样超预留，补账必须单独记账
    async with novel_db() as session:
        await _broker().run(
            session, provider=provider, schema=Echo, work_id=work.id,
            run_id=run_id, chapter_no=1, step="PRODUCE", purpose="plan",
            command_id="v1:plan", system_prompt="sys", user_prompt="user",
        )
        await session.commit()
        total, _ = await _sums(session)
        reservations = (await session.scalars(select(QuotaReservationModel))).all()
        calls_rows = (
            await session.scalars(select(ModelCallModel).order_by(ModelCallModel.attempt))
        ).all()

    expected = sum(int(c.actual_amount_minor or 0) for c in calls_rows)
    assert [c.attempt for c in calls_rows] == [1, 2]
    assert total == expected, f"两次 attempt 合计应为 {expected}，实际 {total}"
    assert all(r.status == "SETTLED" for r in reservations), [
        (r.reservation_key, r.amount_minor, r.settled_minor, r.status) for r in reservations
    ]
