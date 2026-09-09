"""运行版本与租约隔离的行为测试（Plan v6.4 §10 P0-4）。

验收条件对应三条：

1. 租约过期后旧 worker 无法提交——fencing token 与 owner 必须一起复核；
2. 改意前的返回结果不能覆盖新方向——input_version 递增后旧结果作废；
3. 调用输入指纹覆盖模型与采样——同键换模型或换 temperature 是冲突，不是复用。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import production, works
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    ModelCallModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select


class Echo(BaseModel):
    text: str = Field(min_length=1)


class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests: list[dict] = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append(kwargs)
        output = self.outputs.pop(0)
        if not isinstance(output, response_model):
            raise AssertionError(f"{output} is not {response_model}")
        return StructuredModelResponse(
            output=output, usage=ModelUsage(1000, 500), model="test"
        )


def _run(owner: str = "worker:a", token: int = 1, **overrides) -> ChapterRunModel:
    base = dict(
        lease_owner=owner,
        fencing_token=token,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    base.update(overrides)
    return SimpleNamespace(**base)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. 租约隔离
# ---------------------------------------------------------------------------


def test_fencing_token_mismatch_rejects_the_old_worker():
    """新 worker 接管后，旧 worker 持有的 token 立即失效。"""
    run = _run(owner="worker:a", token=7)
    assert production.lease_is_valid(run, owner="worker:a", token=7) is True
    # 他人接管：owner 与 token 都已变
    run.lease_owner = "worker:b"
    run.fencing_token = 8
    assert production.lease_is_valid(run, owner="worker:a", token=7) is False
    with pytest.raises(production.StaleLease):
        production.require_run_lease(run, owner="worker:a", token=7)


def test_expired_lease_is_not_valid_even_for_the_owner():
    """租约过期后，原持有者也不得继续写入。"""
    run = _run(
        owner="worker:a",
        token=3,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert production.lease_is_valid(run, owner="worker:a", token=3) is False
    with pytest.raises(production.StaleLease):
        production.require_run_lease(run, owner="worker:a", token=3)


def test_zero_token_is_never_valid():
    run = _run(owner="worker:a", token=0)
    assert production.lease_is_valid(run, owner="worker:a", token=0) is False


async def test_takeover_bumps_the_token(novel_db, monkeypatch):
    """真实行上：租约过期被接管后，必须拿到更大的 fencing token。"""
    monkeypatch.setattr(works, "append_event", lambda session, **kwargs: None)
    owner = uuid.uuid4()
    async with novel_db() as session:
        session.add(NovelPrincipalModel(id=owner, subject="lease-test"))
        work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑")
        session.add(work)
        await session.flush()
        run = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            state="RUNNING",
        )
        session.add(run)
        await session.flush()
        first = await production.acquire_run_lease(session, run=run, owner="worker:a")
        # 模拟 worker:a 崩溃后租约过期，worker:b 接管
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        second = await production.acquire_run_lease(session, run=run, owner="worker:b")
        assert second > first
        assert production.lease_is_valid(run, owner="worker:b", token=second) is True
        assert production.lease_is_valid(run, owner="worker:a", token=first) is False


# ---------------------------------------------------------------------------
# 2. 输入版本失效
# ---------------------------------------------------------------------------


async def test_input_version_bump_is_recorded(novel_db, monkeypatch):
    """用户改意递增 input_version，并留下可追溯事件。"""
    recorded: list[dict] = []

    async def append_event(session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(works, "append_event", append_event)
    owner = uuid.uuid4()
    async with novel_db() as session:
        session.add(NovelPrincipalModel(id=owner, subject="version-test"))
        work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑")
        session.add(work)
        await session.flush()
        run = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            state="RUNNING",
            input_version=1,
        )
        session.add(run)
        await session.flush()

        assert await works.bump_input_version(
            session, run=run, reason="critical_path_changed"
        ) == 2
        assert await works.bump_input_version(session, run=run, reason="guidance") == 3
        assert run.input_version == 3
        await session.commit()

    async with novel_db() as session:
        stored = await session.scalar(select(ChapterRunModel))
        assert stored.input_version == 3, "改意没有持久化"
    assert sum(
        1 for e in recorded if e.get("event_type") == "chapter.input_version_bumped"
    ) == 2, f"改意没有留痕：{recorded}"


async def test_stale_result_is_discarded_after_the_user_changes_mind(
    novel_db, monkeypatch
):
    """调用窗口内用户改意：旧方向的产出不得写回，步骤不能标成功。"""
    events: list[dict] = []

    async def append_event(session, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(works, "append_event", append_event)

    async def execute_step(session, *, provider, work, run, step):
        """模拟模型调用窗口：期间用户在另一个会话里改意。"""
        async with novel_db() as other:
            target = (await other.scalars(select(ChapterRunModel))).first()
            await works.bump_input_version(
                other, run=target, reason="critical_path_changed"
            )
            await other.commit()
        return True

    monkeypatch.setattr(works, "execute_step", execute_step)

    owner, work_id = uuid.uuid4(), uuid.uuid4()
    async with novel_db() as session:
        session.add(NovelPrincipalModel(id=owner, subject="discard-test"))
        work = StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑")
        session.add(work)
        session.add(
            StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价")
        )
        session.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        path_id = uuid.uuid4()
        session.add(CriticalPathModel(id=path_id, work_id=work_id, node_count=2))
        for ordinal in (1, 2):
            session.add(
                CriticalNodeModel(
                    id=uuid.uuid4(),
                    path_id=path_id,
                    node_id=f"n{ordinal}",
                    ordinal=ordinal,
                    title=f"节点{ordinal}",
                )
            )
        await session.commit()

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()

    async with novel_db() as session:
        await works.advance_step(
            session,
            provider=Provider([]),
            owner_id=owner,
            work_id=work_id,
            chapter_no=1,
        )
        await session.commit()

    async with novel_db() as session:
        run = await session.scalar(select(ChapterRunModel))
        assert run.input_version == 2, "改意没有生效"
        steps = (
            await session.scalars(
                select(works.ChapterStepModel).where(
                    works.ChapterStepModel.run_id == run.id
                )
            )
        ).all()
        assert steps, "没有步骤记录"
        assert not any(s.state == "SUCCEEDED" for s in steps), "旧方向的结果被写成了成功"
    reasons = [
        e["data"].get("reason")
        for e in events
        if e.get("event_type") == "chapter.result_discarded"
    ]
    assert "input_version_changed" in reasons, f"未留下作废留痕：{events}"


# ---------------------------------------------------------------------------
# 3. 调用配置指纹
# ---------------------------------------------------------------------------


def test_config_fingerprint_covers_model_and_sampling():
    base = production.config_fingerprint(model="m", sampling={"temperature": 0})
    assert base != production.config_fingerprint(model="m2", sampling={"temperature": 0})
    assert base != production.config_fingerprint(model="m", sampling={"temperature": 0.7})
    assert base == production.config_fingerprint(model="m", sampling={"temperature": 0})


async def test_same_key_with_different_temperature_is_conflict(novel_db):
    """同键换采样参数：拒绝复用也拒绝合并（§5）。"""
    provider = Provider([Echo(text="第一次"), Echo(text="第二次")])
    key = dict(
        schema=Echo,
        work_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        chapter_no=1,
        step="PRODUCE",
        purpose="plan",
        command_id="v1:plan",
        system_prompt="sys",
        user_prompt="user",
    )
    async with novel_db() as session:
        await production.CallBroker(lease_owner="worker:a").run(
            session, provider=provider, temperature=0.0, model_hint="model-a", **key
        )
        with pytest.raises(production.CallConflict, match="model/sampling"):
            await production.CallBroker(lease_owner="worker:a").run(
                session, provider=provider, temperature=0.9, model_hint="model-a", **key
            )
        with pytest.raises(production.CallConflict, match="model/sampling"):
            await production.CallBroker(lease_owner="worker:a").run(
                session, provider=provider, temperature=0.0, model_hint="model-b", **key
            )
        call = await session.scalar(select(ModelCallModel))
        assert (call.sampling or {}).get("config_hash")
        # 换配置没有产生新的模型调用
        assert len(provider.requests) == 1
