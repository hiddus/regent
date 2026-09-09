"""P1-1：完整命令分发、绑定校验与 Hive 路由（Plan v6.4 §10）。

验收点：

- 非法绑定被拒：命令不能漂移到别的场景或别的 take；
- 完整场景轨迹可重放：manifest 必须带 binding 与 fingerprint；
- 角色上下文互不泄漏：隔离不成立时 Hive 路由关闭并留下证据。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel, Field
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import production
from regent.novel.application.direction import _record_manifest
from regent.novel.application.runtime import (
    NO_SCENE,
    CommandRejected,
    CommandRuntime,
    RuntimeState,
)
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.context import (
    SourceRef,
    compile_actor_context,
    digest,
)
from regent.novel.domain.hive import route_beat
from regent.novel.domain.states import SceneRunState
from regent.novel.infrastructure.models import (
    CostEntryModel,
    ModelCallModel,
    NovelPrincipalModel,
    QuotaReservationModel,
    StoryWorkModel,
)
from sqlalchemy import func, select


class Echo(BaseModel):
    text: str = Field(min_length=1)


class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests: list[dict] = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append(kwargs)
        output = self.outputs.pop(0)
        return StructuredModelResponse(
            output=output, usage=ModelUsage(1000, 500), model="test"
        )


# ---------------------------------------------------------------------------
# 绑定校验
# ---------------------------------------------------------------------------


def _state(**overrides):
    base = dict(
        scene_state=SceneRunState.DIRECTOR_VIEW.value,
        artifact="PROSE",
        has_prose=True,
        cast=frozenset({"主角"}),
        has_visible_events=True,
    )
    base.update(overrides)
    return RuntimeState(**base)


def test_command_bound_to_another_scene_is_rejected():
    rt = CommandRuntime()
    with pytest.raises(CommandRejected, match="场景绑定"):
        rt.validate(
            director_command(
                CommandKind.ACCEPT_SCENE, command_id="x", scene_index=3
            ),
            _state(scene_index=1, take_no=2),
        )


def test_command_bound_to_another_take_is_rejected():
    rt = CommandRuntime()
    with pytest.raises(CommandRejected, match="take 绑定"):
        rt.validate(
            director_command(
                CommandKind.ACCEPT_SCENE, command_id="x", scene_index=1, take_no=1
            ),
            _state(scene_index=1, take_no=2),
        )


def test_planned_scene_requires_no_current_scene():
    """PLAN_SCENE 只在尚无场景时允许，不能借它重置进行中的场景。"""
    rt = CommandRuntime()
    assert rt.validate(
        director_command(CommandKind.PLAN_SCENE, command_id="p"),
        RuntimeState(scene_state=NO_SCENE),
    ) == (SceneRunState.BRIEFED.value, "")
    with pytest.raises(CommandRejected, match="当前阶段"):
        rt.validate(
            director_command(CommandKind.PLAN_SCENE, command_id="p"),
            _state(scene_index=1, take_no=2),
        )


def test_accepting_without_prose_is_rejected():
    rt = CommandRuntime()
    with pytest.raises(CommandRejected, match="没有稿件"):
        rt.validate(
            director_command(CommandKind.ACCEPT_SCENE, command_id="x"),
            _state(has_prose=False),
        )


# ---------------------------------------------------------------------------
# manifest 完整留痕
# ---------------------------------------------------------------------------


@dataclass
class _FakeManifest:
    version: int = 1
    audience: str = "actor"
    persona: str = "主角"
    binding: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    projection_hash: str = ""

    def fingerprint(self) -> str:
        return digest(
            {
                "binding": self.binding,
                "sources": [s.model_dump(mode="json") for s in self.sources],
                "projection_hash": self.projection_hash,
            }
        )


@dataclass
class _FakeCompiled:
    audience: str = "actor"
    persona: str = "主角"
    payload: dict = field(default_factory=dict)
    manifest: _FakeManifest = field(default_factory=_FakeManifest)

    @property
    def manifest_hash(self) -> str:
        return self.manifest.fingerprint()


def test_manifest_records_binding_and_fingerprint():
    """只存 hash 无法回答"这次装配读的是哪一场的哪些材料"。"""
    compiled = _FakeCompiled(
        manifest=_FakeManifest(
            binding={"work_id": "w", "scene_index": "2", "take_no": "1"},
            sources=[SourceRef(kind="canon", ref="parent", hash="h")],
            projection_hash="p",
        )
    )
    take: dict = {}
    _record_manifest(take, compiled)
    stored = take["manifests"][0]
    assert stored["binding"]["scene_index"] == "2"
    assert stored["fingerprint"] == compiled.manifest.fingerprint()
    assert stored["sources"][0]["kind"] == "canon"


# ---------------------------------------------------------------------------
# Hive 路由
# ---------------------------------------------------------------------------


def _actor_context(persona: str, facts: list[dict], observations: list[dict]):
    return compile_actor_context(
        work_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        scene_index=0,
        take_no=1,
        beat=0,
        persona=persona,
        cast={
            "甲": {"identity": "甲的身份", "voice": "甲的语气"},
            "乙": {"identity": "乙的身份", "voice": "乙的语气"},
        },
        direction={"persona": persona, "objective": "试探"},
        setting="旧宅",
        canon=facts,
        observations=observations,
        turn=0,
    )


def test_hive_enables_only_when_isolated_and_concurrent():
    facts = [
        {"statement": "甲的秘密", "known_by": ["甲"]},
        {"statement": "乙的秘密", "known_by": ["乙"]},
        {"statement": "所有人都知道", "known_by": ["ALL"]},
    ]
    contexts = {
        "甲": _actor_context("甲", facts, []),
        "乙": _actor_context("乙", facts, []),
    }
    route = route_beat(contexts)
    assert route.enabled is True
    assert route.reason == "isolated_and_concurrent"
    assert set(route.personas) == {"甲", "乙"}
    # 两个角色看到的不是同一份材料
    assert len(set(route.fingerprints.values())) == 2


def test_single_actor_beat_is_not_hive():
    route = route_beat({"甲": _actor_context("甲", [], [])})
    assert route.enabled is False
    assert route.reason == "single_actor_beat"


def test_leaked_context_disables_hive_with_evidence():
    """只要有一条越界，路由就关闭，并说明是谁看到了什么。"""
    contexts = {
        "甲": _actor_context("甲", [], []),
        "乙": _actor_context("乙", [], []),
    }
    # 人为制造泄露：把只有甲知道的事实塞进乙的上下文
    leaked = contexts["乙"]
    leaked.payload["known_facts"] = [{"statement": "甲的秘密", "known_by": ["甲"]}]
    route = route_beat(contexts)
    assert route.enabled is False
    assert route.reason == "isolation_violated"
    assert any("乙" in leak for leak in route.leaks)


def test_identical_contexts_are_treated_as_not_isolated():
    """两个角色拿到完全一样的上下文，说明裁剪没生效。"""
    shared = SourceRef(kind="canon", ref="parent", hash="h")

    def fake(persona: str):
        return _FakeCompiled(
            persona=persona,
            manifest=_FakeManifest(persona=persona, sources=[shared], projection_hash="same"),
        )

    route = route_beat({"甲": fake("甲"), "乙": fake("乙")})
    assert route.enabled is False
    assert route.reason == "identical_contexts"


# ---------------------------------------------------------------------------
# 批量并发：账本语义必须与逐条调用一致
# ---------------------------------------------------------------------------


async def work_row(session):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"hive-test:{owner}"))
    work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑")
    session.add(work)
    await session.flush()
    return work


async def test_batch_calls_settle_independently(novel_db):
    """并发只覆盖 HTTP 往返；预留、结算与账本必须与逐条调用一致。"""
    provider = Provider([Echo(text="甲的输出"), Echo(text="乙的输出")])
    async with novel_db() as session:
        work = await work_row(session)
        run_id = uuid.uuid4()
        broker = production.CallBroker(lease_owner="worker:h")
        results = await broker.run_batch(
            session,
            provider=provider,
            schema=Echo,
            work_id=work.id,
            run_id=run_id,
            chapter_no=1,
            step="PRODUCE",
            calls=[
                production.BatchCall(
                    purpose="perform",
                    command_id="c1",
                    candidate_id="甲",
                    system_prompt="sys",
                    user_prompt="甲的输入",
                ),
                production.BatchCall(
                    purpose="perform",
                    command_id="c1",
                    candidate_id="乙",
                    system_prompt="sys",
                    user_prompt="乙的输入",
                ),
            ],
        )
        assert [r.output.text for r in results] == ["甲的输出", "乙的输出"]
        calls = (await session.scalars(select(ModelCallModel))).all()
        assert len(calls) == 2
        assert {c.status for c in calls} == {"SUCCEEDED"}
        # 同一节拍不同角色不得撞成同一个逻辑调用
        assert len({c.logical_call_id for c in calls}) == 2
        reservations = (await session.scalars(select(QuotaReservationModel))).all()
        consumed = await session.scalar(
            select(func.sum(CostEntryModel.amount_minor)).where(
                CostEntryModel.entry_kind == "CONSUME"
            )
        )
        released = await session.scalar(
            select(func.sum(CostEntryModel.amount_minor)).where(
                CostEntryModel.entry_kind == "RELEASE"
            )
        )
        billed = sum(int(c.actual_amount_minor or 0) for c in calls)
        assert int(consumed or 0) == billed
        assert sum(int(r.amount_minor) for r in reservations) == int(consumed or 0) + int(
            released or 0
        )


async def test_batch_respects_the_budget_ceiling(novel_db):
    """整批一起预留也不得突破上限。"""
    from regent.novel.domain.errors import QuotaExceeded

    provider = Provider([Echo(text="a"), Echo(text="b")])
    async with novel_db() as session:
        work = await work_row(session)
        broker = production.CallBroker(lease_owner="worker:h", budget_limit_minor=1)
        with pytest.raises(QuotaExceeded):
            await broker.run_batch(
                session,
                provider=provider,
                schema=Echo,
                work_id=work.id,
                run_id=uuid.uuid4(),
                chapter_no=1,
                step="PRODUCE",
                calls=[
                    production.BatchCall(
                        purpose="p", command_id="c", candidate_id="甲",
                        system_prompt="s", user_prompt="u" * 4000,
                    )
                ],
            )
        assert not provider.requests, "预算不足仍发起了调用"
