"""生产调用协议：预留 → 事务外调用 → 结算 → 恢复复用（Tech-Spec §4.4 / §5 / §6）。

规则：

1. **调用前先落库预留**。租约与预留先提交，再发起模型调用；进程在调用中崩溃后
   留下的是 ``RESERVED``/``UNKNOWN`` 记录，而不是一片空白。
2. **模型调用不在事务内进行**。``commit_before_call`` 会在调用前结束当前事务，
   避免长事务把行锁一直握到 HTTP 返回。
3. **外部结果不确定时挂账 ``UNKNOWN``**，保留预留额等待对账；不盲重试。
   没有供应商幂等能力时，盲重试可能产生重复费用（§4.4），因此重复费用会被
   记为一次独立的已结算 attempt，而不是被抹掉。
4. **已成功的 logical call 恢复时复用**：同键同参直接返回首次输出，不再调用、
   不再计费（G-09）。同键异参是冲突（409），不是复用。
5. 金额一律 ``amount_minor: int`` + ``currency``，禁止浮点（G-10）。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeVar

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider, StructuredModelResponse
from regent.novel.application import ledger
from regent.novel.domain.errors import GuardViolation
from regent.novel.domain.price_book import (
    PRICE_BOOK_VERSION,
    actual_minor,
    estimate_minor,
)
from regent.novel.infrastructure.models import ChapterRunModel, ModelCallModel

T = TypeVar("T", bound=BaseModel)

CALL_STATUS_RESERVED = "RESERVED"
CALL_STATUS_SUCCEEDED = "SUCCEEDED"
CALL_STATUS_FAILED = "FAILED"
CALL_STATUS_UNKNOWN = "UNKNOWN"

DEFAULT_LEASE_TTL = timedelta(minutes=10)
# 对账重试上限：超过后按“钱大概率已经花掉”结算并释放，允许后续 attempt 重跑。
DEFAULT_RECONCILE_ATTEMPTS = 3
# 对账静默期：调用刚失败时不立刻按“钱已花掉”结算，给供应商查询留出时间。
DEFAULT_RECONCILE_GRACE = timedelta(seconds=60)


class CallError(RuntimeError):
    """调用协议错误基类。"""

    failure_code = "CALL_ERROR"
    retryable = False


class CallConflict(CallError):
    """同 logical_call_id 但入参不同：幂等冲突，语义等同 409。"""

    failure_code = "CALL_IDEMPOTENCY_CONFLICT"


class CallInFlight(CallError):
    """同一调用仍被他人持有租约。"""

    failure_code = "CALL_IN_FLIGHT"
    retryable = True


class CallUnknown(CallError):
    """外部结果不确定，必须先对账；不得盲重试。"""

    failure_code = "CALL_UNKNOWN"
    retryable = True


class StaleLease(CallError):
    """租约已被别的 worker 接管（或已过期），本次结果不得写入。"""

    failure_code = "STALE_LEASE"
    retryable = True


class CallBudgetExceeded(CallError):
    """货币预算上限（不是调用次数上限）。"""

    failure_code = "CALL_BUDGET_EXCEEDED"


@dataclass(frozen=True, slots=True)
class CallResult:
    output: Any
    logical_call_id: str
    attempt: int
    reserved_minor: int
    actual_minor: int
    reused: bool = False
    avoided_minor: int = 0
    model: str = ""
    request_id: str = ""


_session_factory: Any = None


def configure_session_factory(factory: Any) -> None:
    """装配计费用的独立会话工厂（应用启动时调用一次）。

    预留必须在调用前独立提交，否则进程崩溃后既没有账也没有凭据，
    恢复时只能盲重试（Tech-Spec §4.4）。
    """
    global _session_factory
    _session_factory = factory


def resolve_session_factory(session: Any) -> Any:
    """优先用配置好的工厂；否则从会话的 bind 推一个；都没有则退回本会话。

    退回本会话时账与业务在同一事务里，崩溃后无法区分「没调用」和「调用成功但没记账」，
    只能在日志与文档中标明为降级路径。
    """
    if _session_factory is not None:
        return _session_factory
    engine = _async_engine_of(session)
    if engine is None:
        return None
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


def _async_engine_of(session: Any) -> Any:
    """从会话里取 AsyncEngine；取不到返回 None。

    P0-5：``session.get_bind().engine`` 在异步会话里给的是**同步** Engine，
    拿它去造 AsyncSession 不会在装配时报错，而是在第一次执行时抛
    ``ArgumentError: AsyncEngine expected``——装配漏了要等到运行时才炸。
    取不到就返回 None，让调用方走「同一会话」的降级路径并留痕，而不是硬造。
    """
    from sqlalchemy.ext.asyncio import AsyncEngine

    candidates: list[Any] = [getattr(session, "bind", None)]
    get_bind = getattr(session, "get_bind", None)
    if callable(get_bind):
        with suppress(Exception):  # 无 bind 的桩会话
            candidates.append(get_bind())
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, AsyncEngine):
            return candidate
        engine = getattr(candidate, "engine", None)
        if isinstance(engine, AsyncEngine):
            return engine
    return None


@dataclass(slots=True)
class BatchCall:
    """批量调用中的一次请求。candidate_id 用来区分同节拍的不同角色。"""

    purpose: str
    command_id: str
    candidate_id: str = ""
    system_prompt: str = ""
    user_prompt: str = ""
    temperature: float = 0
    model_hint: str = ""


@dataclass(slots=True)
class _Ticket:
    logical_call_id: str
    attempt: int
    reservation_key: str
    reserved_minor: int
    model_hint: str = ""


@dataclass(slots=True)
class CallBroker:
    """按逻辑调用编排「预留—调用—结算」。

    同一个 broker 实例持有一个租约持有者身份；worker 用 ``worker:<id>``。
    """

    lease_owner: str = "novel"
    lease_ttl: timedelta = DEFAULT_LEASE_TTL
    reconcile_attempts: int = DEFAULT_RECONCILE_ATTEMPTS
    funding_pool: str = "platform"
    # 章节货币上限：预留时做原子校验，避免并发预留一起突破上限（P0-3）。
    budget_limit_minor: int | None = None
    top_ups: list[str] = field(default_factory=list)
    # 计费用独立会话：为 None 时按 resolve_session_factory 解析。
    session_factory: Any = None

    @asynccontextmanager
    async def _tx(self, session: AsyncSession) -> AsyncIterator[AsyncSession]:
        """计费事务：与业务会话分离，块结束时立即提交。"""
        factory = self.session_factory or resolve_session_factory(session)
        if factory is None:
            yield session
            return
        async with factory() as accounting:
            try:
                yield accounting
            except Exception:
                await accounting.rollback()
                raise
            else:
                await accounting.commit()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def run(
        self,
        session: AsyncSession,
        *,
        provider: ModelProvider,
        schema: type[T],
        work_id: uuid.UUID,
        run_id: uuid.UUID | None,
        chapter_no: int | None,
        step: str,
        purpose: str,
        command_id: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0,
        candidate_id: str = "",
        model_hint: str = "",
        commit_before_call: bool = True,
    ) -> CallResult:
        logical_call_id = logical_call_key(run_id, command_id, candidate_id, purpose)
        prompt_hash = _sha(system_prompt)
        context_hash = _sha(user_prompt)
        sampling = {"model": model_hint, "temperature": temperature}
        config_hash = config_fingerprint(model=model_hint, sampling=sampling)

        async with self._tx(session) as acc:
            ticket = await self._prepare(
                acc,
                logical_call_id=logical_call_id,
                work_id=work_id,
                run_id=run_id,
                chapter_no=chapter_no,
                step=step,
                purpose=purpose,
                prompt_hash=prompt_hash,
                context_hash=context_hash,
                model_hint=model_hint,
                sampling=sampling,
                config_hash=config_hash,
                prompt_chars=len(user_prompt),
                system_chars=len(system_prompt),
            )
            if ticket is None:
                return await self._reuse(acc, logical_call_id, schema)

        if commit_before_call and _in_transaction(session):
            # 事务外调用：行锁不再跨越 HTTP 往返，租约保证无人并行推进。
            await session.commit()

        try:
            response = await provider.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=schema,
                temperature=temperature,
            )
        except Exception as exc:
            # 请求可能已经发出：不能假设没花钱，挂账等待对账。
            async with self._tx(session) as acc:
                await self._mark_unknown(
                    acc,
                    ticket,
                    error_code=_error_code(exc),
                    request_id=str(getattr(exc, "request_id", "") or ""),
                )
            raise CallUnknown(
                f"model call result is unknown and must be reconciled: {logical_call_id}"
            ) from exc

        async with self._tx(session) as acc:
            return await self._settle(acc, ticket, response)

    # ------------------------------------------------------------------
    # 阶段一：预留
    # ------------------------------------------------------------------

    async def _prepare(
        self,
        session: AsyncSession,
        *,
        logical_call_id: str,
        work_id: uuid.UUID,
        run_id: uuid.UUID | None,
        chapter_no: int | None,
        step: str,
        purpose: str,
        prompt_hash: str,
        context_hash: str,
        model_hint: str,
        prompt_chars: int,
        system_chars: int,
        sampling: dict[str, Any] | None = None,
        config_hash: str = "",
    ) -> _Ticket | None:
        existing = await _latest_call(session, logical_call_id)
        if existing is not None:
            if existing.prompt_hash != prompt_hash or existing.context_hash != context_hash:
                raise CallConflict(
                    "same logical call id with different inputs; refusing to merge (§5)"
                )
            stored_config = (existing.sampling or {}).get("config_hash")
            if stored_config and stored_config != config_hash:
                # 同键换了模型或采样参数：不是同一次调用，不得复用也不得合并。
                raise CallConflict(
                    "same logical call id with different model/sampling; refusing to merge (§5)"
                )
            if (
                existing.status == CALL_STATUS_SUCCEEDED
                and existing.output_json is not None
            ):
                return None  # 复用
            if existing.status == CALL_STATUS_UNKNOWN:
                raise CallUnknown(f"unreconciled call: {logical_call_id}")
            if existing.status == CALL_STATUS_RESERVED:
                if _lease_live(existing):
                    if existing.lease_owner == self.lease_owner:
                        # 自己上一轮崩溃留下的租约：同样不可判定。
                        raise CallUnknown(f"interrupted call: {logical_call_id}")
                    raise CallInFlight(f"call already leased: {logical_call_id}")
                await _reap(session, existing)
                raise CallUnknown(f"expired lease, call unknown: {logical_call_id}")

        attempt = 1 if existing is None else int(existing.attempt) + 1
        reserved = estimate_minor(
            model_hint,
            prompt_chars=prompt_chars,
            system_chars=system_chars,
        )
        reservation_key = f"{logical_call_id}:{attempt}"
        await ledger.reserve(
            session,
            reservation_key=reservation_key,
            work_id=work_id,
            amount_minor=reserved,
            logical_call_id=logical_call_id,
            chapter_no=chapter_no,
            ttl=self.lease_ttl,
            funding_limit_minor=self.budget_limit_minor,
        )
        now = datetime.now(UTC)
        session.add(
            ModelCallModel(
                id=uuid.uuid4(),
                logical_call_id=logical_call_id,
                work_id=work_id,
                run_id=run_id,
                chapter_no=chapter_no,
                step=step,
                purpose=purpose,
                cost_scope="generation",
                provider="openai_compatible",
                model=model_hint,
                prompt_hash=prompt_hash,
                context_hash=context_hash,
                sampling={
                    "model": model_hint,
                    "temperature": (sampling or {}).get("temperature", 0),
                    "config_hash": config_hash,
                },
                status=CALL_STATUS_RESERVED,
                attempt=attempt,
                reserved_amount_minor=reserved,
                currency="CNY",
                price_book_version=PRICE_BOOK_VERSION,
                lease_owner=self.lease_owner,
                lease_expires_at=now + self.lease_ttl,
                lease_ttl_seconds=int(self.lease_ttl.total_seconds()),
                usage_source="estimated",
                updated_at=now,
            )
        )
        await session.flush()
        return _Ticket(
            logical_call_id=logical_call_id,
            attempt=attempt,
            reservation_key=reservation_key,
            reserved_minor=reserved,
            model_hint=model_hint,
        )

    # ------------------------------------------------------------------
    # 阶段二：结算 / 复用 / 挂账
    # ------------------------------------------------------------------

    async def _settle(
        self, session: AsyncSession, ticket: _Ticket, response: StructuredModelResponse[T]
    ) -> CallResult:
        usage = response.usage
        actual = actual_minor(
            response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=getattr(usage, "cached_input_tokens", 0) or 0,
        )
        call = await _call_row(session, ticket.logical_call_id, ticket.attempt)
        if call is None:
            raise GuardViolation(f"model call row missing: {ticket.logical_call_id}")
        call.model = response.model
        call.input_tokens = int(usage.input_tokens)
        call.output_tokens = int(usage.output_tokens)
        call.cached_input_tokens = int(getattr(usage, "cached_input_tokens", 0) or 0)
        call.provider_request_id = getattr(usage, "request_id", "") or ""
        await self._finalize(
            session,
            call,
            status=CALL_STATUS_SUCCEEDED,
            actual=actual,
            output_json=response.output.model_dump(mode="json"),
            usage_source="provider",
        )
        return CallResult(
            output=response.output,
            logical_call_id=ticket.logical_call_id,
            attempt=ticket.attempt,
            reserved_minor=ticket.reserved_minor,
            actual_minor=actual,
            model=response.model,
            request_id=getattr(usage, "request_id", "") or "",
        )

    async def _finalize(
        self,
        session: AsyncSession,
        call: ModelCallModel,
        *,
        status: str,
        actual: int,
        output_json: dict | None = None,
        usage_source: str,
        error_code: str | None = None,
    ) -> None:
        """统一的终态结算：按实际额消费、释放余额，**按 attempt 幂等**。

        幂等键必须带 attempt：否则第二次尝试的费用会被第一次的记录吞掉，
        账本与调用金额对不上（P0-2）。真实已发生费用即使超过预留也要补记，
        不因超限丢账。
        """
        reserved = int(call.reserved_amount_minor or 0)
        billed = min(actual, reserved)
        ledger_id = f"{call.logical_call_id}:{call.attempt}"
        reservation_key = f"{call.logical_call_id}:{call.attempt}"

        call.status = status
        call.actual_amount_minor = actual
        call.usage_source = usage_source
        if output_json is not None:
            call.output_json = output_json
            call.output_hash = _sha(_dump(output_json))
        if error_code:
            call.error_code = error_code
        call.lease_owner = None
        call.lease_expires_at = None
        call.updated_at = datetime.now(UTC)

        if billed > 0:
            await ledger.consume(
                session,
                reservation_key=reservation_key,
                amount_minor=billed,
                work_id=call.work_id,
                chapter_no=call.chapter_no,
                step=call.step,
                logical_call_id=ledger_id,
                funding_pool=self.funding_pool,
                price_book_version=PRICE_BOOK_VERSION,
            )
        if actual > reserved:
            # 预留不足：补记真实已发生费用，不抹平差额。
            await self._top_up(session, call=call, amount=actual - reserved)
        await ledger.release(
            session,
            reservation_key=reservation_key,
            work_id=call.work_id,
            chapter_no=call.chapter_no,
            step=call.step,
            logical_call_id=ledger_id,
            funding_pool=self.funding_pool,
        )
        await session.flush()

    async def _top_up(self, session: AsyncSession, *, call: ModelCallModel, amount: int) -> None:
        """预留不足时补记真实已发生费用。

        A-01：消费键必须与预留键一样带 attempt。预留是 ``{id}:{attempt}:topup``
        而消费是 ``{id}:topup`` 时，第二次 attempt 的补账会被第一次的幂等记录
        吞掉——钱真花出去了，账上只记一次。
        """
        key = f"{call.logical_call_id}:{call.attempt}:topup"
        self.top_ups.append(key)
        await ledger.reserve(
            session,
            reservation_key=key,
            work_id=call.work_id,
            amount_minor=amount,
            logical_call_id=key,
            chapter_no=call.chapter_no,
            ttl=self.lease_ttl,
        )
        await ledger.consume(
            session,
            reservation_key=key,
            amount_minor=amount,
            work_id=call.work_id,
            chapter_no=call.chapter_no,
            step=call.step,
            logical_call_id=key,
            funding_pool=self.funding_pool,
            price_book_version=PRICE_BOOK_VERSION,
        )

    async def _mark_unknown(
        self,
        session: AsyncSession,
        ticket: _Ticket,
        *,
        error_code: str,
        request_id: str = "",
    ) -> None:
        call = await _call_row(session, ticket.logical_call_id, ticket.attempt)
        if call is None:
            return
        call.status = CALL_STATUS_UNKNOWN
        call.error_code = error_code
        # 超时类异常往往带着供应商 request_id：留下来，对账才有得可查。
        if request_id and not call.provider_request_id:
            call.provider_request_id = request_id[:64]
        call.lease_expires_at = None
        call.updated_at = datetime.now(UTC)
        await session.flush()

    async def _reuse(
        self, session: AsyncSession, logical_call_id: str, schema: type[T]
    ) -> CallResult:
        call = await _latest_call(session, logical_call_id)
        assert call is not None  # 由 _prepare 保证
        output = schema.model_validate(call.output_json)
        return CallResult(
            output=output,
            logical_call_id=logical_call_id,
            attempt=int(call.attempt),
            reserved_minor=int(call.reserved_amount_minor or 0),
            actual_minor=int(call.actual_amount_minor or 0),
            reused=True,
            avoided_minor=int(call.actual_amount_minor or call.reserved_amount_minor or 0),
            model=call.model,
            request_id=call.provider_request_id or "",
        )

    async def run_batch(
        self,
        session: AsyncSession,
        *,
        provider: ModelProvider,
        schema: type[T],
        work_id: uuid.UUID,
        run_id: uuid.UUID | None,
        chapter_no: int | None,
        step: str,
        calls: list[BatchCall],
        commit_before_call: bool = True,
    ) -> list[CallResult]:
        """同节拍多角色并发：预留与结算仍顺序进行，只有模型调用并发（Hive）。

        会话不能并发使用，因此**只把 HTTP 往返并发掉**：预留、结算、失败挂账
        全部顺序执行，账本语义与逐条调用完全一致。预算按逐个递减的剩余额度
        校验，避免整批一起突破上限。
        """
        prepared: list[tuple[str, _Ticket | None, BatchCall]] = []
        saved_limit = self.budget_limit_minor
        remaining = saved_limit
        try:
            for spec in calls:
                logical_call_id = logical_call_key(
                    run_id, spec.command_id, spec.candidate_id, spec.purpose
                )
                sampling = {"model": spec.model_hint, "temperature": spec.temperature}
                if remaining is not None:
                    self.budget_limit_minor = max(int(remaining), 0)
                async with self._tx(session) as acc:
                    ticket = await self._prepare(
                        acc,
                        logical_call_id=logical_call_id,
                        work_id=work_id,
                        run_id=run_id,
                        chapter_no=chapter_no,
                        step=step,
                        purpose=spec.purpose,
                        prompt_hash=_sha(spec.system_prompt),
                        context_hash=_sha(spec.user_prompt),
                        model_hint=spec.model_hint,
                        sampling=sampling,
                        config_hash=config_fingerprint(
                            model=spec.model_hint, sampling=sampling
                        ),
                        prompt_chars=len(spec.user_prompt),
                        system_chars=len(spec.system_prompt),
                    )
                if ticket is not None and remaining is not None:
                    remaining = int(remaining) - int(ticket.reserved_minor)
                prepared.append((logical_call_id, ticket, spec))
        finally:
            self.budget_limit_minor = saved_limit

        if commit_before_call and _in_transaction(session):
            await session.commit()

        async def _invoke(spec: BatchCall) -> Any:
            try:
                return await provider.generate_structured(
                    system_prompt=spec.system_prompt,
                    user_prompt=spec.user_prompt,
                    response_model=schema,
                    temperature=spec.temperature,
                )
            except Exception as exc:  # 请求可能已发出：挂账，不猜结果
                return exc

        pending = [spec for _, ticket, spec in prepared if ticket is not None]
        responses = await asyncio.gather(*(_invoke(spec) for spec in pending))
        by_index = iter(responses)

        results: list[CallResult] = []
        unknown: BaseException | None = None
        for logical_call_id, ticket, _spec in prepared:
            if ticket is None:
                async with self._tx(session) as acc:
                    results.append(await self._reuse(acc, logical_call_id, schema))
                continue
            response = next(by_index)
            if isinstance(response, BaseException):
                async with self._tx(session) as acc:
                    await self._mark_unknown(
                        acc,
                        ticket,
                        error_code=_error_code(response),
                        request_id=str(getattr(response, "request_id", "") or ""),
                    )
                if unknown is None:
                    unknown = response
                continue
            async with self._tx(session) as acc:
                results.append(await self._settle(acc, ticket, response))

        if unknown is not None:
            # 已成功的结果保留在账上，未知的那一条挂账等待对账（§4.4）。
            raise CallUnknown(
                "batch contains an unknown model call result that must be reconciled"
            ) from unknown
        return results

    # ------------------------------------------------------------------
    # 对账
    # ------------------------------------------------------------------

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        logical_call_id: str,
        provider: ModelProvider | None = None,
    ) -> Literal["SUCCEEDED", "FAILED", "PENDING", "UNKNOWN_CALL"]:
        """尝试确定 UNKNOWN 调用的外部结果。

        供应商提供查询能力时据实结算（并结清预留、留下费用流水）；否则按
        **对账次数**达到上限后按“费用已发生”结算，让后续 attempt 可以重跑——
        重复费用留在账上，不抹除（§4.4）。

        返回值中的 ``PENDING`` 只在未达对账上限时出现，默认配置下必定有界结束。
        """
        async with self._tx(session) as acc:
            call = await _latest_call(acc, logical_call_id)
            if call is None:
                return "UNKNOWN_CALL"
            if call.status != CALL_STATUS_UNKNOWN:
                return "SUCCEEDED" if call.status == CALL_STATUS_SUCCEEDED else "FAILED"

            lookup = getattr(provider, "lookup_call", None)
            if callable(lookup) and call.provider_request_id:
                found = await lookup(call.provider_request_id)
                if found is not None:
                    # 查到结果就必须结清：只改状态会留下悬空预留（P0-2）。
                    actual = actual_minor(
                        call.model,
                        input_tokens=int(found.get("input_tokens", 0)),
                        output_tokens=int(found.get("output_tokens", 0)),
                    )
                    output = found.get("output")
                    await self._finalize(
                        acc,
                        call,
                        status=(
                            CALL_STATUS_SUCCEEDED if output is not None else CALL_STATUS_FAILED
                        ),
                        actual=actual,
                        output_json=output,
                        usage_source="provider_reconciled",
                        error_code=None if output is not None else "PROVIDER_NO_OUTPUT",
                    )
                    return "SUCCEEDED" if output is not None else "FAILED"

            # 无查询能力：按**对账次数**终止，不用 attempt——attempt 只在新一次
            # 模型调用时递增，拿它当上限会让 UNKNOWN 永远挂起（P0-1）。
            call.reconcile_count = int(call.reconcile_count or 0) + 1
            if call.reconcile_count >= self.reconcile_attempts:
                await self._settle_unknown(acc, call)
                return "FAILED"
            await acc.flush()
        return "PENDING"

    async def _settle_unknown(self, session: AsyncSession, call: ModelCallModel) -> None:
        """放弃对账：按“费用已发生”结算预留额，不留悬账（§4.4）。"""
        amount = int(call.reserved_amount_minor or 0)
        await self._finalize(
            session,
            call,
            status=CALL_STATUS_FAILED,
            actual=amount,
            usage_source="estimated",
            error_code="RECONCILE_EXHAUSTED",
        )


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def logical_call_key(
    production_id: Any, command_id: str, candidate_id: str, purpose: str
) -> str:
    """§5 幂等键 ``production_id:command_id:candidate_id:purpose``。"""
    key = f"{production_id}:{command_id}:{candidate_id}:{purpose}"
    if len(key) <= 255:
        return key
    return f"{production_id}:sha256:{_sha(key)}"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def config_fingerprint(*, model: str, sampling: dict[str, Any] | None = None) -> str:
    """调用配置指纹：模型与采样参数参与同键判定（Tech-Spec §5）。

    只覆盖 prompt/context 会让「同键不同模型或不同 temperature」被当成同一次
    调用而复用或冲突判定失真，因此配置必须进指纹。
    """
    return _sha(_dump({"model": model, "sampling": dict(sampling or {})}))


def _dump(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _in_transaction(session: Any) -> bool:
    checker = getattr(session, "in_transaction", None)
    if not callable(checker):
        return True
    try:
        return bool(checker())
    except Exception:
        return True


def _lease_live(call: ModelCallModel) -> bool:
    expires = call.lease_expires_at
    if expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > datetime.now(UTC)


def _error_code(exc: BaseException) -> str:
    return (getattr(exc, "failure_code", None) or type(exc).__name__)[:64]


async def _latest_call(session: AsyncSession, logical_call_id: str) -> ModelCallModel | None:
    rows = await session.scalars(
        select(ModelCallModel)
        .where(ModelCallModel.logical_call_id == logical_call_id)
        .order_by(ModelCallModel.attempt.desc())
        .limit(1)
    )
    if hasattr(rows, "first"):
        return rows.first()
    for row in rows:  # pragma: no cover - 兼容简单桩对象
        return row
    return None


async def _call_row(
    session: AsyncSession, logical_call_id: str, attempt: int
) -> ModelCallModel | None:
    return await session.scalar(
        select(ModelCallModel).where(
            ModelCallModel.logical_call_id == logical_call_id,
            ModelCallModel.attempt == attempt,
        )
    )


async def _reap(session: AsyncSession, call: ModelCallModel) -> None:
    """租约过期：判定为 UNKNOWN，保留预留额等待对账，禁止盲重试（§4.4）。"""
    call.status = CALL_STATUS_UNKNOWN
    call.error_code = "LEASE_EXPIRED"
    call.lease_expires_at = None
    call.updated_at = datetime.now(UTC)
    await session.flush()


async def reclaim_expired_calls(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 50
) -> list[str]:
    """把租约过期仍停留在 RESERVED 的调用判定为 UNKNOWN。"""
    moment = now or datetime.now(UTC)
    rows = (
        await session.scalars(
            select(ModelCallModel)
            .where(
                ModelCallModel.status == CALL_STATUS_RESERVED,
                ModelCallModel.lease_expires_at.is_not(None),
                ModelCallModel.lease_expires_at <= moment,
            )
            .limit(limit)
        )
    ).all()
    for call in rows:
        call.status = CALL_STATUS_UNKNOWN
        call.error_code = "LEASE_EXPIRED"
        call.lease_expires_at = None
        call.updated_at = moment
    if rows:
        await session.flush()
    return [str(call.logical_call_id) for call in rows]


async def recover_novel_calls(
    session: AsyncSession,
    *,
    provider: ModelProvider | None = None,
    lease_owner: str = "recovery",
    limit: int = 50,
    grace: timedelta = DEFAULT_RECONCILE_GRACE,
    reconcile_attempts: int = DEFAULT_RECONCILE_ATTEMPTS,
) -> dict[str, int]:
    """恢复清扫：把崩溃留下的调用收口，供 worker 每次启动/周期 tick 调用（P0-1）。

    两步，顺序不能反：

    1. 租约过期仍停在 ``RESERVED`` 的调用 → ``UNKNOWN``，保留预留额等待对账；
    2. ``UNKNOWN`` 调用 → 对账：供应商可查就据实结算（成功结果之后会被复用），
       查不到就按**对账次数**有界终止，按“费用已发生”结算，允许后续 attempt 重跑。

    只处理静默期之前的调用，避免把刚刚失败、供应商还没来得及落账的调用提前结清。
    返回计数供 worker 打点；单个调用对账失败由调用方记录，不得拖垮整个 tick。
    """
    stats = {"reclaimed": 0, "succeeded": 0, "failed": 0, "pending": 0, "skipped": 0}
    bucket = {
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "PENDING": "pending",
    }
    factory = resolve_session_factory(session)

    async def _sweep(acc: AsyncSession, *, reuse: bool) -> None:
        """在同一个会话里「回收 → 提交 → 对账」。

        P0-5：回收若只在调用方事务里 flush，而对账走独立账本会话，对账根本
        看不到 UNKNOWN（读到的是已提交的 RESERVED），既不结清也不改状态；随后
        调用方提交旧快照，又把状态覆盖回去。两步必须在同一连接上且中间提交。
        """
        broker = CallBroker(
            lease_owner=lease_owner,
            reconcile_attempts=reconcile_attempts,
            # 对账必须复用这条连接，否则看不到刚回收出来的 UNKNOWN。
            session_factory=_FixedSession(acc) if reuse else None,
        )
        stats["reclaimed"] = len(await reclaim_expired_calls(acc, limit=limit))
        await acc.commit()

        cutoff = datetime.now(UTC) - grace
        rows = (
            await acc.scalars(
                select(ModelCallModel)
                .where(
                    ModelCallModel.status == CALL_STATUS_UNKNOWN,
                    or_(
                        ModelCallModel.updated_at.is_(None),
                        ModelCallModel.updated_at <= cutoff,
                    ),
                )
                .order_by(ModelCallModel.updated_at)
                .limit(limit)
            )
        ).all()
        for call in rows:
            outcome = await broker.reconcile(
                acc, logical_call_id=str(call.logical_call_id), provider=provider
            )
            stats[bucket.get(outcome, "skipped")] += 1
        await acc.commit()

    if factory is None:
        # 降级路径：没有独立账本会话，回收与对账本来就在同一事务里。
        await _sweep(session, reuse=False)
        return stats

    async with factory() as acc:
        await _sweep(acc, reuse=True)
    return stats


class _FixedSession:
    """把已打开的会话包成 ``CallBroker._tx`` 期望的工厂。

    让回收与对账复用同一条连接：各自开会话会互相看不见对方的未提交改动（P0-5）。
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    def __call__(self) -> _FixedSession:
        return self

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False

# ---------------------------------------------------------------------------
# 运行租约：让“事务外调用”期间别的 worker 不会并行推进同一章
# ---------------------------------------------------------------------------


async def acquire_run_lease(
    session: AsyncSession,
    *,
    run: ChapterRunModel,
    owner: str,
    ttl: timedelta = DEFAULT_LEASE_TTL,
) -> int:
    """领取运行租约并返回 fencing token。租约在期且属于他人时抛 CallInFlight。"""
    now = datetime.now(UTC)
    expires = run.lease_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires is not None and expires > now and run.lease_owner != owner:
        raise CallInFlight(f"chapter run leased by {run.lease_owner}")
    run.lease_owner = owner
    run.lease_expires_at = now + ttl
    run.fencing_token = int(run.fencing_token or 0) + 1
    await session.flush()
    return int(run.fencing_token)


async def release_run_lease(session: AsyncSession, *, run: ChapterRunModel, owner: str) -> None:
    if run.lease_owner not in (None, "", owner):
        return
    run.lease_owner = None
    run.lease_expires_at = None
    await session.flush()


def lease_is_valid(
    run: ChapterRunModel,
    *,
    owner: str,
    token: int,
    now: datetime | None = None,
) -> bool:
    """租约是否仍然属于当前持有者（P0-4）。

    模型调用在事务外进行，调用期间别的 worker 可能已接管：写回结果前必须
    复核 owner、fencing token 与有效期，三者缺一不可。
    """
    if int(token or 0) <= 0:
        return False
    if run.lease_owner != owner:
        return False
    if int(run.fencing_token or 0) != int(token):
        return False
    moment = now or datetime.now(UTC)
    expires = run.lease_expires_at
    if expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > moment


def require_run_lease(
    run: ChapterRunModel, *, owner: str, token: int, now: datetime | None = None
) -> None:
    """租约失效即拒绝写入：旧 worker 不得覆盖新 worker 的方向。"""
    if not lease_is_valid(run, owner=owner, token=token, now=now):
        raise StaleLease(
            f"chapter run lease lost: owner={run.lease_owner} token={run.fencing_token}"
        )


def lease_is_free(run: ChapterRunModel, *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(UTC)
    expires = run.lease_expires_at
    if expires is None:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= moment


__all__ = [
    "CALL_STATUS_FAILED",
    "CALL_STATUS_RESERVED",
    "CALL_STATUS_SUCCEEDED",
    "CALL_STATUS_UNKNOWN",
    "DEFAULT_LEASE_TTL",
    "DEFAULT_RECONCILE_ATTEMPTS",
    "DEFAULT_RECONCILE_GRACE",
    "CallBroker",
    "CallBudgetExceeded",
    "CallConflict",
    "CallError",
    "CallInFlight",
    "BatchCall",
    "CallResult",
    "StaleLease",
    "CallUnknown",
    "acquire_run_lease",
    "lease_is_free",
    "lease_is_valid",
    "require_run_lease",
    "config_fingerprint",
    "logical_call_key",
    "reclaim_expired_calls",
    "recover_novel_calls",
    "release_run_lease",
]
