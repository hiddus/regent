"""一次性补丁：调用失败时保留供应商 request_id，使恢复可查询（P0-1）。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/production.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


sub(
    """    async def _mark_unknown(
        self, session: AsyncSession, ticket: _Ticket, *, error_code: str
    ) -> None:
        call = await _call_row(session, ticket.logical_call_id, ticket.attempt)
        if call is None:
            return
        call.status = CALL_STATUS_UNKNOWN
        call.error_code = error_code
        call.lease_expires_at = None
        call.updated_at = datetime.now(UTC)
        await session.flush()
""",
    '''    async def _mark_unknown(
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
''',
)

sub(
    """            async with self._tx(session) as acc:
                await self._mark_unknown(acc, ticket, error_code=_error_code(exc))
""",
    """            async with self._tx(session) as acc:
                await self._mark_unknown(
                    acc,
                    ticket,
                    error_code=_error_code(exc),
                    request_id=str(getattr(exc, "request_id", "") or ""),
                )
""",
)

path.write_text(text, encoding="utf-8")
print("patched", path)
