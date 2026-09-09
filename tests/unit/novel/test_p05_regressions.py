"""P0-5 真机认证抓到的三个缺陷的回归测试。

这三个问题都只在**真实 PostgreSQL 并发/多连接**下暴露，SQLite 单连接测不出来，
因此这里按「机制」而不是「现象」写断言：

1. 账本会话工厂拿同步 Engine 硬造 AsyncSession —— 装配时不报错，运行时才炸；
2. 恢复清扫的「回收」与「对账」不在同一会话 —— 对账看不到刚回收出来的 UNKNOWN，
   既不结清也不改状态，调用方提交旧快照又把状态覆盖回去；
3. 0048 迁移按当前 ORM metadata 建表 —— 与 0049~0052 抢建同样的表/列，
   全新库在升级链上直接撞 DuplicateTable。
"""

from __future__ import annotations

import importlib.util
import pathlib
import uuid
from datetime import timedelta

import pytest
from regent.novel.application import production
from regent.novel.infrastructure.models import ModelCallModel, NovelBase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[3]
    / "core"
    / "migrations"
    / "versions"
    / "20260903_0048_novel_domain.py"
)


def _load_0048():
    spec = importlib.util.spec_from_file_location("novel_migration_0048", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_session_factory_never_yields_sync_engine():
    """P0-5：同步 Engine 造出来的 AsyncSession 要到第一次执行才炸。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(NovelBase.metadata.create_all)
        previous = production._session_factory
        production.configure_session_factory(None)
        try:
            async with async_sessionmaker(engine)() as session:
                factory = production.resolve_session_factory(session)
                assert factory is not None
                async with factory() as accounting:
                    assert await accounting.scalar(select(1)) == 1
        finally:
            production.configure_session_factory(previous)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_reclaims_and_reconciles_in_one_session(novel_db, monkeypatch):
    """P0-5：回收与对账必须同一会话，否则对账看不到 UNKNOWN。"""
    seen: dict[str, int] = {}
    original_reclaim = production.reclaim_expired_calls

    async def fake_reclaim(session, limit=50):
        seen["reclaim"] = id(session)
        return []

    async def fake_reconcile(self, session, *, logical_call_id, provider=None):
        seen["reconcile"] = id(session)
        return "PENDING"

    monkeypatch.setattr(production, "reclaim_expired_calls", fake_reclaim)
    monkeypatch.setattr(production.CallBroker, "reconcile", fake_reconcile)
    assert original_reclaim is not None

    # 先放一条真正待对账的 UNKNOWN，否则查询不到行、对账根本不会被调用。
    work_id = uuid.uuid4()
    async with novel_db() as s:
        s.add(ModelCallModel(
            id=uuid.uuid4(),
            logical_call_id="recovery:visibility:probe",
            work_id=work_id,
            step="PRODUCE",
            purpose="probe",
            provider="test",
            model="test",
            status=production.CALL_STATUS_UNKNOWN,
            reconcile_count=0,
        ))
        await s.commit()

    async with novel_db() as caller:
        await production.recover_novel_calls(caller, grace=timedelta(0))
    assert "reconcile" in seen, "待对账的 UNKNOWN 没有被对账"
    assert seen["reclaim"] == seen["reconcile"], "回收与对账必须在同一会话里"


def test_0048_does_not_own_later_migration_objects():
    """0048 的冻结清单必须覆盖「模型当前形状」与「0048 时代形状」的全部差异。"""
    migration = _load_0048()
    frozen: dict[str, frozenset[str]] = migration._FROZEN_COLUMNS
    later_tables: frozenset[str] = migration._LATER_TABLES
    later_columns: dict[str, frozenset[str]] = migration._LATER_COLUMNS

    for table in NovelBase.metadata.sorted_tables:
        if table.name in later_tables:
            assert table.name not in frozen
            continue
        assert table.name in frozen, f"{table.name} 没有冻结清单，0048 会静默建错"
        current = {column.name for column in table.columns}
        drift = current - set(frozen[table.name])
        assert drift <= later_columns.get(table.name, frozenset()), (
            f"{table.name} 漂移了 {sorted(drift)}：改模型请新增迁移，不要改 0048"
        )


def test_0048_frozen_tables_are_known():
    migration = _load_0048()
    frozen = migration._FROZEN_COLUMNS
    known = {table.name for table in NovelBase.metadata.sorted_tables}
    assert set(frozen) == known - migration._LATER_TABLES
    for name in ("novel_volumes", "novel_arc_nodes"):
        assert name in migration._LATER_TABLES
    assert _MIGRATION.exists()


def test_logical_call_key_stable():
    """幂等键仍是 §5 的四段式，配置指纹不参与键本身。"""
    key = production.logical_call_key(uuid.uuid4(), "cmd", "cand", "plan")
    assert key.count(":") == 3
