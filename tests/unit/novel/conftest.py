"""小说域测试夹具：为生产调用协议提供真实的计费库。

预留与结算走独立会话（production.CallBroker），因此测试需要一个真实的
SQLite 库来承载 ModelCall / 额度预留 / 成本流水；业务断言仍可用桩会话。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from regent.novel.application import production
from regent.novel.infrastructure.models import NovelBase


@pytest.fixture(autouse=True)
async def novel_db() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(NovelBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    production.configure_session_factory(factory)
    try:
        yield factory
    finally:
        production.configure_session_factory(None)
        await engine.dispose()
