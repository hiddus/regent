"""A-03：末节点完成与跨卷必须闭合到 v2 成章分支（Plan v6.9 第一批）。

审计反例：`_maybe_expand_volume()` 只在「步骤全部 SUCCEEDED」的旧分支被调用，
director_v2 在 CANON 步骤就提前返回，于是 v2 永不扩卷；`story_complete` 要等
下一章 ASSEMBLE 才发现前章末节点已完成，`start_run` 只查上一章既存的
story_complete，于是会先多开一章没有目标节点的任务。

验收要求：从真实 v2 生成流程走到最后一个节点成章，再由 `start_run` 进入下
一步，不手工设置 story_complete。
"""

# Chinese fixture prose deliberately uses full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import uuid

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application import works
from regent.novel.application.generation import StoryOutline, StoryOutlineNode
from regent.novel.domain.errors import Conflict
from regent.novel.domain.states import StoryWorkState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ChapterStepModel,
    CriticalNodeModel,
    CriticalPathModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
    VolumeModel,
)
from sqlalchemy import select

from test_direction import (  # noqa: E402
    Provider,
    TEXT,
    brief,
    prose_decision,
    resolution,
    take_decision,
    validation,
)


def _volume_outline(volume_title="第二卷") -> StoryOutline:
    """下一卷的大纲：扩卷必须走真实大纲，不能靠静态模板兜底（B-05）。"""
    return StoryOutline(
        volume_title=volume_title,
        nodes=[
            StoryOutlineNode(
                title=f"新卷节点{i}",
                node_type="ESCALATION",
                promise="承上启下",
            )
            for i in range(1, 11)
        ],
    )


class _Provider(Provider):
    """导演终局判断与卷大纲都由 fixture 显式给出。

    两条都不能从 outputs 队列里取：终局判断会在成章收尾时才发生，取队列会把
    下一章的模型输出吃掉，把「判定没发生」伪装成「模型调用不稳定」。
    """

    def __init__(self, outputs, *, ending_complete=None, outline=None):
        super().__init__(outputs)
        self.ending_complete = ending_complete
        self.outline = outline

    async def generate_structured(self, *, response_model, **kwargs):
        if response_model is StoryOutline:
            if self.outline is None:
                raise RuntimeError("fixture has no volume outline")
            return StructuredModelResponse(
                output=self.outline, usage=ModelUsage(10, 20), model="test"
            )
        if getattr(response_model, "__name__", "") == "EndingVerdict":
            if self.ending_complete is None:
                raise RuntimeError("fixture has no ending verdict")
            return StructuredModelResponse(
                output=response_model(
                    story_complete=bool(self.ending_complete),
                    reason="核心冲突已在正文中收束",
                ),
                usage=ModelUsage(10, 20),
                model="test",
            )
        return await super().generate_structured(response_model=response_model, **kwargs)


def _chapter_outputs(node_completed: bool) -> list:
    """一章从规划到成章所需的模型输出。"""
    return [
        d.ChapterDirection(
            title="交付",
            reader_intent="为信任担心",
            ending_reason="交付已成立",
            scenes=[brief()],
        ),
        d.ActorTurn(intention="信任", actions=["伸手"], private_reasoning="PRIVATE"),
        resolution(),
        take_decision(),
        d.SceneText(content=TEXT),
        prose_decision(),
        validation(),
        d.ChapterValidation(
            passed=True,
            node_completed=node_completed,
            completion_quote="他把钥匙放在桌上。",
        ),
    ]


async def _noop_event(session, **kwargs):
    return None


def _capture_events(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    async def _fake(session, **kwargs):
        captured.append(kwargs)
        return None

    monkeypatch.setattr(works, "append_event", _fake)
    return captured


async def _boot(
    sessions, *, node_count: int = 2, end_chapter: int = 6, ending_target_volume: int = 0
):
    """一部有一卷、若干节点的作品；节点数与卷范围都是显式给出的。

    ``ending_target_volume`` 是用户认可的终局：它是完结判定的第一依据，给了它
    就不再问模型——问模型等于把用户的数字降级成一个建议（B-05）。
    """
    owner, work_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as session:
        session.add(NovelPrincipalModel(id=owner, subject=f"a03:{owner}"))
        session.add(
            StoryWorkModel(
                id=work_id,
                owner_id=owner,
                state="READY",
                genre="悬疑",
                total_volume_count=1,
                ending_target_volume=ending_target_volume,
            )
        )
        session.add(
            StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价")
        )
        session.add(
            PersonaSpecModel(
                id=uuid.uuid4(), work_id=work_id, name="主角", voice={"style": "克制"}
            )
        )
        session.add(
            VolumeModel(
                id=uuid.uuid4(),
                work_id=work_id,
                volume_no=1,
                title="第一卷",
                start_chapter_no=1,
                end_chapter_no=end_chapter,
                state="ACTIVE",
            )
        )
        path_id = uuid.uuid4()
        session.add(CriticalPathModel(id=path_id, work_id=work_id, node_count=node_count))
        for ordinal in range(1, node_count + 1):
            session.add(
                CriticalNodeModel(
                    id=uuid.uuid4(),
                    path_id=path_id,
                    node_id=f"n{ordinal}",
                    ordinal=ordinal,
                    title=f"节点{ordinal}",
                    volume_no=1,
                )
            )
        await session.commit()
    return owner, work_id


async def _run_chapter(sessions, provider, owner, work_id, chapter_no) -> None:
    for _ in range(40):
        async with sessions() as session:
            progress = await works.advance_step(
                session,
                provider=provider,
                owner_id=owner,
                work_id=work_id,
                chapter_no=chapter_no,
            )
            await session.commit()
            if progress.state.value == "CANONIZED":
                return
    async with sessions() as session:
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id,
                ChapterRunModel.chapter_no == chapter_no,
            )
        )
        steps = [
            (s.step, s.state, s.error_code)
            for s in (
                await session.scalars(
                    select(ChapterStepModel).where(ChapterStepModel.run_id == run.id)
                )
            ).all()
        ]
    pytest.fail(f"第 {chapter_no} 章没有走到 CANONIZED：{run.state} {steps}")


async def _node_ids(sessions, work_id) -> list[str]:
    async with sessions() as session:
        path = await session.scalar(
            select(CriticalPathModel).where(CriticalPathModel.work_id == work_id)
        )
        return list(
            (
                await session.scalars(
                    select(CriticalNodeModel.node_id)
                    .where(CriticalNodeModel.path_id == path.id)
                    .order_by(CriticalNodeModel.ordinal)
                )
            ).all()
        )


async def _volumes(sessions, work_id) -> list[VolumeModel]:
    async with sessions() as session:
        return list(
            (
                await session.scalars(
                    select(VolumeModel)
                    .where(VolumeModel.work_id == work_id)
                    .order_by(VolumeModel.volume_no)
                )
            ).all()
        )


async def test_last_node_expands_volume_and_next_chapter_has_a_target(
    novel_db, monkeypatch
):
    """末节点成章后必须扩卷，下一章有真实目标节点，不是空章。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(
        _chapter_outputs(True) + _chapter_outputs(True) + _chapter_outputs(False),
        outline=_volume_outline(),
    )
    # 用户要写两卷：第一卷末节点完成后应当继续扩卷
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6, ending_target_volume=2)

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 1)

    # 第一节点完成、不是末节点：不该扩卷，也不该结束
    assert await _node_ids(novel_db, work_id) == ["n1", "n2"]
    assert len(await _volumes(novel_db, work_id)) == 1

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 2)

    # 末节点成章：v2 也必须扩卷（审计前这里不会发生）
    node_ids = await _node_ids(novel_db, work_id)
    assert len(node_ids) > 2, f"末节点完成后没有扩卷：{node_ids}"
    volumes = await _volumes(novel_db, work_id)
    assert [int(v.volume_no) for v in volumes] == [1, 2]
    assert int(volumes[1].start_chapter_no) == 3, "新卷起点不是下一章"

    async with novel_db() as session:
        work = await session.get(StoryWorkModel, work_id)
        assert work.state == StoryWorkState.RUNNING.value, "扩出卷了却把整本结束了"
        progress = await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    assert progress.chapter_no == 3

    # 第三章必须拿到新卷里的目标节点，而不是空 target
    await _run_chapter(novel_db, provider, owner, work_id, 3)
    async with novel_db() as session:
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 3
            )
        )
        target = (run.generation_context or {}).get("target_node", {})
    assert target.get("id"), f"第三章没有目标节点：{target}"
    assert target["id"] in node_ids[2:], f"第三章仍指向旧节点：{target}"
    assert (run.generation_context or {}).get("volume", {}).get("volume_no") == 2


async def test_story_ends_when_user_target_volume_is_reached(novel_db, monkeypatch):
    """用户说写几卷就写几卷：最后一卷写完即结束，不必先试扩卷再失败。

    这条用例刻意**不**替换 expand_next_volume：完结必须是从正常生产入口判出来的，
    而不是把扩卷函数换成恒返回 None 之后也能 DONE（B-05）。
    """
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(_chapter_outputs(True) + _chapter_outputs(True))
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6, ending_target_volume=1)

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 1)
    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 2)

    async with novel_db() as session:
        work = await session.get(StoryWorkModel, work_id)
        assert work.state == StoryWorkState.DONE.value, f"整本没有结束：{work.state}"
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 2
            )
        )
        assert (run.generation_context or {}).get("story_complete") is True, (
            "结束标记没有落在刚成章的那一次运行上"
        )
        with pytest.raises(Conflict):
            await works.start_run(session, owner_id=owner, work_id=work_id)

    async with novel_db() as session:
        assert (
            await session.scalar(
                select(ChapterRunModel).where(
                    ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 3
                )
            )
        ) is None, "整本结束后仍创建了新章节"


async def test_director_can_end_the_story_when_user_gave_no_volume_target(
    novel_db, monkeypatch
):
    """用户没说写几卷时，由导演判断终局是否达成：判达成即结束，且不新增空章。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(
        _chapter_outputs(True) + _chapter_outputs(True), ending_complete=True
    )
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6)

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 1)
    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 2)

    async with novel_db() as session:
        work = await session.get(StoryWorkModel, work_id)
        assert work.state == StoryWorkState.DONE.value, f"导演判定了终局却没有结束：{work.state}"
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 2
            )
        )
        decision = (run.generation_context or {}).get("ending_decision") or {}
        assert decision.get("basis") == "director_verdict", decision
        with pytest.raises(Conflict):
            await works.start_run(session, owner_id=owner, work_id=work_id)


async def test_expansion_failure_keeps_the_story_open_instead_of_rewriting_it(
    novel_db, monkeypatch
):
    """B-05：该继续但扩卷失败时，保留待定状态，不套模板也不算完结。"""
    events = _capture_events(monkeypatch)
    # 用户要三卷，当前第一卷：判定必须继续，但大纲拿不到
    provider = _Provider(_chapter_outputs(True) + _chapter_outputs(True))
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6, ending_target_volume=3)

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 1)
    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 2)

    async with novel_db() as session:
        work = await session.get(StoryWorkModel, work_id)
        assert work.state != StoryWorkState.DONE.value, "扩卷失败被当成了完结"
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 2
            )
        )
        decision = (run.generation_context or {}).get("ending_decision") or {}
    assert decision.get("choice") == "undecided", decision
    assert any(
        e.get("event_type") == "volume.expansion_failed" for e in events
    ), "扩卷失败没有留下可恢复的事件记录"
    # 没有新卷、也没有凭空多出一章
    assert [int(v.volume_no) for v in await _volumes(novel_db, work_id)] == [1]
