"""Novel 仓储端口（Tech-Spec §1.2 / §2）。

应用层只依赖这些协议；实现由 ``infrastructure`` 提供。
端口的意义在于把「领域行为」与「数据库实现」隔开：更换存储不影响
状态机、影响预览、账本与导出守卫的语义。
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from regent.novel.domain.models import CriticalPathOut, EventPage


class WorkRepository(Protocol):
    async def get_owned(self, work_id: uuid.UUID, owner_id: uuid.UUID) -> Any:
        """按 owner 读取；不存在或越权返回 None（不泄露存在性，G-12）。"""

    async def list_owned(self, owner_id: uuid.UUID) -> list[Any]: ...

    async def version(self, work_id: uuid.UUID) -> int: ...


class CriticalPathRepository(Protocol):
    async def current(self, work_id: uuid.UUID) -> CriticalPathOut: ...

    async def version(self, work_id: uuid.UUID) -> int: ...


class EventStore(Protocol):
    """持久事件序列（G-17）。"""

    async def append(
        self,
        *,
        work_id: uuid.UUID,
        event_type: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    async def read_after(
        self, *, work_id: uuid.UUID, after_seq: int, limit: int = 200
    ) -> EventPage: ...

    async def last_sequence(self, work_id: uuid.UUID) -> int: ...


class CostLedger(Protocol):
    """金额最小单位、两段式额度、幂等结算（G-10 / FR-18 / FR-19）。"""

    async def reserve(
        self, *, reservation_key: str, work_id: uuid.UUID, amount_minor: int, **kwargs: Any
    ) -> Any: ...

    async def consume(
        self, *, reservation_key: str, amount_minor: int, work_id: uuid.UUID, **kwargs: Any
    ) -> Any: ...

    async def release(self, *, reservation_key: str, work_id: uuid.UUID, **kwargs: Any) -> Any: ...

    async def work_cost(self, work_id: uuid.UUID, currency: str = "CNY") -> dict[str, Any]: ...


class ContentRenderer(Protocol):
    """导出渲染。实现必须确定性：不得经过 LLM（G-15）。"""

    def render(self, *, work: Any, chapters: list[Any], fmt: str) -> str: ...
