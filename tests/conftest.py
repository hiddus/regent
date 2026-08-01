"""Shared pytest fixtures: in-memory SQLite session factory for governance tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from regent.infrastructure.models import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(PGUUID, "sqlite")
def _compile_uuid_sqlite(type_: PGUUID, compiler: object, **kw: object) -> str:
    return "CHAR(36)"


def pytest_configure(config: pytest.Config) -> None:
    """QA gate: isolate xdist workers under .pytest_tmp/<worker>."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    basetemp = Path(".pytest_tmp") / worker
    basetemp.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(basetemp)


@pytest.fixture
async def db_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Real async sessionmaker backed by SQLite (JSONB/UUID compiled for sqlite)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
