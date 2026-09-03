"""成本、额度与账本（Tech-Spec §6 / G-08 / G-09 / G-10 / FR-18 / FR-19）。

规则：
- 金额一律 ``amount_minor: int`` + ``currency``，**禁止浮点**。
- 额度两段式 ``RESERVE -> CONSUME/RELEASE``；失败或取消释放未消费额。
- ``ModelCall`` 是成本事实源；``CostEntry`` 为 append-only 流水。
- 幂等键 ``logical_call_id:funding_pool``；已成功的 logical call 恢复时复用，不重新付费。
- 任何条件账户更新必须检查 row count，否则事务失败（Tech-Spec §6）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.domain.errors import GuardViolation, QuotaExceeded
from regent.novel.domain.models import FundingSource
from regent.novel.domain.money import validate_pair
from regent.novel.infrastructure.models import (
    CostEntryModel,
    ModelCallModel,
    QuotaReservationModel,
)

# generation scope 必须有 work/chapter/step；reading scope 禁止生成（G-14）
GENERATION_SCOPE = "generation"
READING_SCOPE = "reading"


class LedgerError(RuntimeError):
    """账本不一致——属于服务端缺陷，不是用户输入错误。"""


# ---------------------------------------------------------------------------
# 模型调用（成本事实源）
# ---------------------------------------------------------------------------


async def record_model_call(
    session: AsyncSession,
    *,
    logical_call_id: str,
    work_id: uuid.UUID,
    purpose: str,
    provider: str,
    model: str,
    prompt_hash: str,
    context_hash: str,
    cost_scope: str = GENERATION_SCOPE,
    run_id: uuid.UUID | None = None,
    chapter_no: int | None = None,
    step: str = "",
    prompt_version: str = "",
    sampling: dict[str, Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    output_hash: str = "",
    price_book_version: str = "novel-price-book-v1",
    status: str = "SUCCEEDED",
) -> ModelCallModel:
    """幂等写入模型调用。同 logical_call_id 复用，不重复计费（G-09）。"""
    if cost_scope == READING_SCOPE:
        raise GuardViolation("reading scope must not trigger generation calls")
    if not work_id or not purpose:
        raise GuardViolation("model call requires work_id and purpose (G-08)")

    existing = await session.scalar(
        select(ModelCallModel).where(ModelCallModel.logical_call_id == logical_call_id)
    )
    if existing is not None:
        return existing

    if input_tokens and not output_hash:
        # usage 来源必须可追溯；缺 output_hash 会让成本无法复核
        output_hash = f"pending:{logical_call_id}"

    call = ModelCallModel(
        logical_call_id=logical_call_id,
        work_id=work_id,
        run_id=run_id,
        chapter_no=chapter_no,
        step=step,
        purpose=purpose,
        cost_scope=cost_scope,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        context_hash=context_hash,
        sampling=sampling or {},
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        output_hash=output_hash,
        price_book_version=price_book_version,
    )
    session.add(call)
    await session.flush()
    return call


# ---------------------------------------------------------------------------
# 额度：reserved -> consumed / released
# ---------------------------------------------------------------------------


async def reserve(
    session: AsyncSession,
    *,
    reservation_key: str,
    work_id: uuid.UUID,
    amount_minor: int,
    currency: str = "CNY",
    logical_call_id: str = "",
    chapter_no: int | None = None,
    ttl: timedelta = timedelta(hours=2),
    funding_limit_minor: int | None = None,
) -> QuotaReservationModel:
    """预留额度。幂等：同 key 返回既有预留。超限时抛 QuotaExceeded（429 可重试）。"""
    amount_minor, currency = validate_pair(amount_minor, currency)

    existing = await session.scalar(
        select(QuotaReservationModel).where(
            QuotaReservationModel.reservation_key == reservation_key
        )
    )
    if existing is not None:
        return existing

    if funding_limit_minor is not None:
        outstanding = await _outstanding_minor(session, work_id=work_id, currency=currency)
        if outstanding + amount_minor > funding_limit_minor:
            raise QuotaExceeded(
                "work quota ceiling would be exceeded",
                retry_after=60,
            )

    row = QuotaReservationModel(
        reservation_key=reservation_key,
        work_id=work_id,
        chapter_no=chapter_no,
        logical_call_id=logical_call_id,
        amount_minor=amount_minor,
        currency=currency,
        status="RESERVED",
        expires_at=datetime.now(UTC) + ttl,
    )
    session.add(row)
    await session.flush()
    return row


async def consume(
    session: AsyncSession,
    *,
    reservation_key: str,
    amount_minor: int,
    work_id: uuid.UUID,
    chapter_no: int | None = None,
    step: str = "",
    logical_call_id: str = "",
    funding_pool: str = "platform",
    funding_source: FundingSource = FundingSource.PLATFORM_GRANT,
    currency: str = "CNY",
    price_book_version: str = "novel-price-book-v1",
) -> CostEntryModel:
    """结算预留（按实际成本）。幂等键 logical_call_id:funding_pool。

    条件更新必须检查 row count，否则说明被并发改坏 → 事务失败，不静默。
    """
    amount_minor, currency = validate_pair(amount_minor, currency)

    idem_key = f"{logical_call_id}:{funding_pool}"
    existing = await session.scalar(
        select(CostEntryModel).where(
            CostEntryModel.logical_call_id == logical_call_id,
            CostEntryModel.funding_pool == funding_pool,
            CostEntryModel.entry_kind == "CONSUME",
        )
    )
    if existing is not None:
        return existing

    res = await session.scalar(
        select(QuotaReservationModel)
        .where(QuotaReservationModel.reservation_key == reservation_key)
        .with_for_update()
    )
    if res is None:
        raise GuardViolation(f"reservation not found: {reservation_key}")
    if amount_minor > res.amount_minor:
        raise GuardViolation("consume exceeds reservation amount")

    # 条件更新：settled + amount 不得超过预留额
    result = await session.execute(
        text(
            "UPDATE novel_quota_reservations "
            "SET settled_minor = settled_minor + :amount, "
            "    status = CASE WHEN settled_minor + :amount >= amount_minor "
            "                  THEN 'SETTLED' ELSE status END "
            "WHERE reservation_key = :key AND settled_minor + :amount <= amount_minor "
            "RETURNING settled_minor"
        ),
        {"amount": amount_minor, "key": reservation_key},
    )
    if result.rowcount != 1:
        raise LedgerError("conditional quota update affected unexpected row count")

    entry = CostEntryModel(
        work_id=work_id,
        chapter_no=chapter_no,
        step=step,
        logical_call_id=logical_call_id or idem_key,
        funding_pool=funding_pool,
        funding_source=funding_source.value,
        amount_minor=amount_minor,
        currency=currency,
        entry_kind="CONSUME",
        price_book_version=price_book_version,
    )
    session.add(entry)
    await session.flush()
    return entry


async def release(
    session: AsyncSession,
    *,
    reservation_key: str,
    work_id: uuid.UUID,
    chapter_no: int | None = None,
    step: str = "",
    logical_call_id: str = "",
    funding_pool: str = "platform",
    funding_source: FundingSource = FundingSource.PLATFORM_GRANT,
    currency: str = "CNY",
) -> CostEntryModel | None:
    """释放未消费额度。幂等：已 SETTLED 的预留不再释放。"""
    res = await session.scalar(
        select(QuotaReservationModel)
        .where(QuotaReservationModel.reservation_key == reservation_key)
        .with_for_update()
    )
    if res is None:
        return None
    remaining = int(res.amount_minor) - int(res.settled_minor)
    if remaining <= 0:
        return None

    result = await session.execute(
        text(
            "UPDATE novel_quota_reservations "
            "SET settled_minor = amount_minor, status = 'RELEASED' "
            "WHERE reservation_key = :key AND status = 'RESERVED'"
        ),
        {"key": reservation_key},
    )
    if result.rowcount not in (0, 1):
        raise LedgerError("conditional quota release affected unexpected row count")

    entry = CostEntryModel(
        work_id=work_id,
        chapter_no=chapter_no,
        step=step,
        logical_call_id=logical_call_id or reservation_key,
        funding_pool=funding_pool,
        funding_source=funding_source.value,
        amount_minor=remaining,
        currency=currency,
        entry_kind="RELEASE",
    )
    session.add(entry)
    await session.flush()
    return entry


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


async def _outstanding_minor(
    session: AsyncSession, *, work_id: uuid.UUID, currency: str
) -> int:
    rows = await session.execute(
        text(
            "SELECT COALESCE(SUM(amount_minor - settled_minor), 0) FROM novel_quota_reservations "
            "WHERE work_id = :work_id AND currency = :currency AND status = 'RESERVED'"
        ),
        {"work_id": work_id, "currency": currency},
    )
    return int(rows.scalar_one())


async def work_cost(
    session: AsyncSession, *, work_id: uuid.UUID, currency: str = "CNY"
) -> dict[str, Any]:
    """按作品聚合成本：可归因至作品、章、节拍（FR-19）。余额由流水派生。"""
    total = await session.execute(
        text(
            "SELECT COALESCE(SUM(amount_minor), 0) FROM novel_cost_entries "
            "WHERE work_id = :work_id AND currency = :currency AND entry_kind = 'CONSUME'"
        ),
        {"work_id": work_id, "currency": currency},
    )
    released = await session.execute(
        text(
            "SELECT COALESCE(SUM(amount_minor), 0) FROM novel_cost_entries "
            "WHERE work_id = :work_id AND currency = :currency AND entry_kind = 'RELEASE'"
        ),
        {"work_id": work_id, "currency": currency},
    )
    by_chapter = await session.execute(
        text(
            "SELECT chapter_no, COALESCE(SUM(amount_minor), 0) AS total FROM novel_cost_entries "
            "WHERE work_id = :work_id AND currency = :currency AND entry_kind = 'CONSUME' "
            "GROUP BY chapter_no ORDER BY chapter_no"
        ),
        {"work_id": work_id, "currency": currency},
    )
    by_step = await session.execute(
        text(
            "SELECT step, COALESCE(SUM(amount_minor), 0) AS total FROM novel_cost_entries "
            "WHERE work_id = :work_id AND currency = :currency AND entry_kind = 'CONSUME' "
            "GROUP BY step ORDER BY step"
        ),
        {"work_id": work_id, "currency": currency},
    )
    by_source = await session.execute(
        text(
            "SELECT funding_source, COALESCE(SUM(amount_minor), 0) AS total "
            "FROM novel_cost_entries "
            "WHERE work_id = :work_id AND currency = :currency AND entry_kind = 'CONSUME' "
            "GROUP BY funding_source"
        ),
        {"work_id": work_id, "currency": currency},
    )
    return {
        "currency": currency,
        "consumed_minor": int(total.scalar_one()),
        "released_minor": int(released.scalar_one()),
        "by_chapter": [
            {"chapter_no": r[0], "amount_minor": int(r[1])} for r in by_chapter.all()
        ],
        "by_step": [{"step": r[0], "amount_minor": int(r[1])} for r in by_step.all()],
        "by_funding_source": [
            {"funding_source": r[0], "amount_minor": int(r[1])} for r in by_source.all()
        ],
    }
