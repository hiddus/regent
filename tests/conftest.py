"""Shared pytest fixtures: in-memory SQLite session factory for governance tests."""

from __future__ import annotations

import pytest
from collections.abc import AsyncIterator

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
