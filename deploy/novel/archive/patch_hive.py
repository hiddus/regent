"""一次性补丁：CallBroker 批量并发调用（Hive 局部执行器）。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/production.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


sub(
    "import hashlib\nimport uuid\n",
    "import asyncio\nimport hashlib\nimport uuid\n",
)

BATCH = '''
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
        for logical_call_id, ticket, spec in prepared:
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
'''

sub(
    """
    # ------------------------------------------------------------------
    # 对账
    # ------------------------------------------------------------------
""",
    BATCH
    + """
    # ------------------------------------------------------------------
    # 对账
    # ------------------------------------------------------------------
""",
)

# BatchCall 数据结构
sub(
    '''@dataclass(slots=True)
class _Ticket:''',
    '''@dataclass(slots=True)
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
class _Ticket:''',
)

sub(
    '    "CallResult",\n    "StaleLease",\n',
    '    "BatchCall",\n    "CallResult",\n    "StaleLease",\n',
)

path.write_text(text, encoding="utf-8")
print("patched", path)
