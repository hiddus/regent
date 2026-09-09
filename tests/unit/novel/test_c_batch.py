"""C-01～C-05 的入口级验收（Plan v7.4）。

这层的命题都指向「如果实现退回旧行为就会失败」的具体断言，而不是「字段存在」
这种永远为真的检查。每条都对应完备性复核里已复现的一个缺口。

- C-02：扩卷入口不能绕过用户终局约束，也不能提前结束当前卷跳过剩余节点。

Chinese fixture prose deliberately uses full-width punctuation.
ruff: noqa: RUF001
"""

from __future__ import annotations

import json
import uuid

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import generation, works
from regent.novel.application import memory as memory_app
from regent.novel.domain.errors import InvalidState
from regent.novel.domain.models import ReportFactRequest
from regent.novel.domain.states import ChapterRunState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    NovelPrincipalModel,
    StoryWorkModel,
    VolumeModel,
)
from sqlalchemy import select


async def _chapter_runs(session, work: StoryWorkModel, count: int) -> None:
    for chapter_no in range(1, count + 1):
        session.add(
            ChapterRunModel(
                id=uuid.uuid4(),
                work_id=work.id,
                branch_id=work.branch_id,
                chapter_no=chapter_no,
                attempt=1,
                state="CANONIZED",
            )
        )
    await session.flush()


def _fact(statement: str, **extra) -> dict:
    """正式核验输出：只有 statement/quote/known_by，没有分类标记。"""
    return {"statement": statement, "quote": statement, "known_by": ["甲"], **extra}


async def _work(session, *, latest_chapter_no: int = 0,
                ending_target_volume: int = 0) -> StoryWorkModel:
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
    work = StoryWorkModel(
        id=uuid.uuid4(),
        owner_id=owner,
        state="RUNNING",
        genre="悬疑",
        latest_chapter_no=latest_chapter_no,
        ending_target_volume=ending_target_volume,
    )
    session.add(work)
    await session.flush()
    return work


async def _active_volume(session, work, *, volume_no: int = 1,
                         start: int = 1, end: int = 10) -> VolumeModel:
    vol = VolumeModel(
        id=uuid.uuid4(),
        work_id=work.id,
        volume_no=volume_no,
        title=f"第{volume_no}卷",
        start_chapter_no=start,
        end_chapter_no=end,
        state="ACTIVE",
    )
    session.add(vol)
    await session.flush()
    return vol


async def _path_with_nodes(session, work, node_ids=("n1", "n2", "n3")):
    path = CriticalPathModel(id=uuid.uuid4(), work_id=work.id, node_count=len(node_ids))
    session.add(path)
    await session.flush()
    for ordinal, node_id in enumerate(node_ids, start=1):
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(),
                path_id=path.id,
                node_id=node_id,
                ordinal=ordinal,
                title=f"节点{ordinal}",
            )
        )
    await session.flush()
    return path


def _last_node_run(work, *, chapter_no: int = 5) -> ChapterRunModel:
    """一次「刚写完末节点」的运行：node_completed + target_node 为最后一个节点。"""
    return ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        state=ChapterRunState.CANONIZED.value,
        generation_context={"node_completed": True, "target_node": {"id": "n3"}},
    )


# ---------------------------------------------------------------------------
# C-02 终局约束
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eighty_percent_progress_does_not_close_the_active_volume(novel_db, monkeypatch):
    """80% 预规划不得扩卷：扩卷会把当前活动卷标记完成，剩余节点就被跳过了（C-02）。"""
    expanded: list[object] = []

    async def fake_expand(session, *, work, provider=None):
        expanded.append(work.id)
        return None

    monkeypatch.setattr(works, "expand_next_volume", fake_expand)

    async with novel_db() as session:
        work = await _work(session, latest_chapter_no=8, ending_target_volume=1)
        await _active_volume(session, work, volume_no=1, start=1, end=10)
        await _path_with_nodes(session, work)
        # 不传 run：当前卷的末节点还没写完，只因为完成度到了 80%
        await works._maybe_expand_volume(session, work=work)

    assert expanded == [], "完成度 80% 就扩卷，等于提前结束当前卷、跳过 n2/n3"


@pytest.mark.asyncio
async def test_user_volume_cap_blocks_expansion_on_last_node(novel_db, monkeypatch):
    """用户限定一卷时，即便末节点完成也不得新建第二卷（C-02）。"""
    expanded: list[object] = []

    async def fake_expand(session, *, work, provider=None):
        expanded.append(work.id)
        return None

    monkeypatch.setattr(works, "expand_next_volume", fake_expand)

    async with novel_db() as session:
        work = await _work(session, latest_chapter_no=8, ending_target_volume=1)
        await _active_volume(session, work, volume_no=1, start=1, end=10)
        await _path_with_nodes(session, work)
        run = _last_node_run(work)
        session.add(run)
        await session.flush()
        await works._maybe_expand_volume(session, work=work, run=run)

    assert expanded == [], "用户只给了 1 卷，末节点完成后仍新建了第二卷"


@pytest.mark.asyncio
async def test_last_node_still_expands_when_user_set_no_cap(novel_db, monkeypatch):
    """对照组：用户没限定卷数时，末节点完成仍要扩卷——不能把扩卷路径整个删掉（P1-3）。"""
    expanded: list[object] = []

    async def fake_expand(session, *, work, provider=None):
        expanded.append(work.id)
        return None

    monkeypatch.setattr(works, "expand_next_volume", fake_expand)

    async with novel_db() as session:
        work = await _work(session, latest_chapter_no=5, ending_target_volume=0)
        await _active_volume(session, work, volume_no=1, start=1, end=100)
        await _path_with_nodes(session, work)
        run = _last_node_run(work)
        session.add(run)
        await session.flush()
        await works._maybe_expand_volume(session, work=work, run=run)

    assert expanded == [work.id], "无卷数上限且末节点已完成，却没有扩卷"


# ---------------------------------------------------------------------------
# C-01 纠错执行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_run_carries_the_correction(novel_db, monkeypatch):
    """纠错内容必须随任务走到导演请求里，不能只躺在事件里（C-01）。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    correction = "甲其实从未持有钥匙"

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        await _chapter_runs(s, work, 3)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await works.report_fact(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=ReportFactRequest(
                statement=correction, chapter_no=1, subject="甲"
            ),
        )
        await s.commit()

        replay = (
            await s.scalars(
                select(ChapterRunModel).where(
                    ChapterRunModel.work_id == work.id,
                    ChapterRunModel.chapter_no == 1,
                    ChapterRunModel.attempt == 2,
                )
            )
        ).one_or_none()

    assert replay is not None, "没有为受影响章节建立重演运行"
    ctx = dict(replay.generation_context or {})
    assert ctx.get("replay_reason") == "fact_reported"
    assert (ctx.get("correction") or {}).get("statement") == correction, (
        f"重演运行没有携带纠错内容，导演无从知道要改什么：{ctx}"
    )


@pytest.mark.asyncio
async def test_done_work_does_not_promise_automatic_replay(novel_db, monkeypatch):
    """作品已完结时后台不会领取任务，因此不能回「已受理，系统会自动重演」（C-01）。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        work.state = "DONE"
        await _chapter_runs(s, work, 3)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        response = await works.report_fact(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=ReportFactRequest(
                statement="甲其实从未持有钥匙", chapter_no=1, subject="甲"
            ),
        )
        await s.commit()
        state_after = str(work.state)

    assert response.available_actions == ["resume_then_replay"], (
        f"完结作品仍承诺自动重演：{response.available_actions}"
    )
    assert "恢复创作" in response.message, response.message
    # 保持完结意图：不因为一次报错就把作品悄悄改回 RUNNING
    assert state_after == "DONE", f"作品状态被静默改成了 {state_after}"


@pytest.mark.asyncio
async def test_repeated_reports_do_not_stack_tasks(novel_db, monkeypatch):
    """同一章重复报错不得无限叠新的重演任务（C-01）。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        await _chapter_runs(s, work, 3)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        payload = ReportFactRequest(
            statement="甲其实从未持有钥匙", chapter_no=1, subject="甲"
        )
        for _ in range(3):
            await works.report_fact(
                s, owner_id=work.owner_id, work_id=work.id, payload=payload
            )
            await s.commit()

        attempts = sorted(
            int(r.attempt)
            for r in (
                await s.scalars(
                    select(ChapterRunModel).where(
                        ChapterRunModel.work_id == work.id,
                        ChapterRunModel.chapter_no == 1,
                    )
                )
            ).all()
        )

    assert attempts == [1, 2], f"重复报错把任务堆成了 {attempts}"


@pytest.mark.asyncio
async def test_done_work_can_be_resumed_to_run_its_queued_replay(novel_db, monkeypatch):
    """完结作品的纠错重演，在用户明确恢复后必须真的能被执行（C-01 闭环）。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        work.state = "DONE"
        await _chapter_runs(s, work, 3)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        response = await works.report_fact(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=ReportFactRequest(
                statement="甲其实从未持有钥匙", chapter_no=1, subject="甲"
            ),
        )
        await s.commit()
        ticket = response.ticket_id
        # 用户明确恢复：这一步之前，后台不会领取该作品的任何任务
        out = await works.resume_after_correction(
            s, owner_id=work.owner_id, work_id=work.id, ticket_id=ticket
        )
        await s.commit()
        state_after = str(work.state)

    assert str(out) == "RUNNING", out
    assert state_after == "RUNNING"


@pytest.mark.asyncio
async def test_resume_without_queued_replay_is_rejected(novel_db, monkeypatch):
    """没有可执行的重演任务时不把作品空放回 RUNNING——那只会留下「在跑但没在跑」（C-01）。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        # 用「允许恢复」的状态，确保拒绝只可能来自「没有排队任务」这道闸门，
        # 否则状态守卫会顶掉它，测试就退化成「只要抛错就算过」。
        work.state = "PAUSED_QUOTA"
        await _chapter_runs(s, work, 3)
        await s.commit()
        with pytest.raises(InvalidState, match="queued"):
            await works.resume_after_correction(
                s, owner_id=work.owner_id, work_id=work.id
            )
        await s.commit()

    assert str(work.state) == "PAUSED_QUOTA", "没有排队任务却把作品恢复了"


# ---------------------------------------------------------------------------
# C-04 依赖独立性证据
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disjoint_entities_are_not_auto_certified_independent(novel_db):
    """实体无交集不等于独立：启发式没命中必须保持 unknown，不能宣称图完整（C-04）。"""
    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        # 第一批：此刻还没有任何记忆，「没有上游」是结构性事实
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1,
            facts=[{"statement": "甲承诺归还钥匙", "known_by": ["甲"]}],
            cast=["甲", "乙"],
        )
        await s.commit()
        # 第二批：关于乙，与已有的甲记忆没有任何共享实体
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2,
            facts=[{"statement": "乙立誓复仇", "known_by": ["乙"]}],
            cast=["甲", "乙"],
        )
        await s.commit()
        plan = await memory_app.plan_replay(s, work=work, changed_subjects=["甲"])

    assert not plan.complete, (
        "实体无交集被自动认证为独立，于是宣称依赖图完整——这正是 C-04 要堵住的推断"
    )
    assert plan.unknown, "没有独立性证据的条目应落在 unknown 里"


@pytest.mark.asyncio
async def test_declared_independent_is_certified(novel_db):
    """创作输入显式声明独立，才算有独立性证据（C-04）。"""
    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1,
            facts=[{"statement": "甲承诺归还钥匙", "known_by": ["甲"]}],
            cast=["甲", "乙"],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2,
            facts=[{
                "statement": "乙立誓复仇", "known_by": ["乙"],
                # 创作输入显式声明：这条与既有记忆没有依赖
                "independent": True,
            }],
            cast=["甲", "乙"],
        )
        await s.commit()
        plan = await memory_app.plan_replay(s, work=work, changed_subjects=["甲"])

    assert plan.complete, f"已显式声明独立却仍判图不完整：{plan.unknown}"


# ---------------------------------------------------------------------------
# C-03 正式兑现链
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paying_off_one_promise_keeps_the_other_open(novel_db):
    """同一人的两条承诺，只兑现一条时另一条必须保留（C-03）。"""
    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1,
            facts=[
                {"statement": "甲承诺明日归还钥匙", "known_by": ["甲"]},
                {"statement": "甲立誓三年后复仇", "known_by": ["甲"]},
            ],
            cast=["甲"],
        )
        await s.commit()
        # 正文明确写出「归还钥匙」这一条兑现了
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2,
            facts=[{
                "statement": "甲把钥匙还给乙，兑现承诺", "known_by": ["甲"],
                "resolves": "甲承诺明日归还钥匙",
            }],
            cast=["甲"],
        )
        await s.commit()
        bundle = await memory_app.recall_memory(
            s, work=work, chapter_no=2, kinds=("promise",)
        )

    states = {item.content: item.state for item in bundle.items}
    assert states.get("甲承诺明日归还钥匙") == "RESOLVED", states
    assert states.get("甲立誓三年后复仇") == "OPEN", (
        f"只兑现了一条，另一条却被一起关掉了：{states}"
    )


@pytest.mark.asyncio
async def test_subject_only_payoff_does_not_close_ambiguous_promises(novel_db):
    """只给人物名的兑现是有歧义的：同人多承诺时不得整批关闭（C-03）。"""
    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1,
            facts=[
                {"statement": "甲承诺明日归还钥匙", "known_by": ["甲"]},
                {"statement": "甲立誓三年后复仇", "known_by": ["甲"]},
            ],
            cast=["甲"],
        )
        await s.commit()
        # 只说「甲」兑现了，没说是哪一条：两条都还挂着才对
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2,
            facts=[{"statement": "甲了结了心事", "known_by": ["甲"], "resolves": "甲"}],
            cast=["甲"],
        )
        await s.commit()
        bundle = await memory_app.recall_memory(
            s, work=work, chapter_no=2, kinds=("promise",)
        )

    states = {item.content: item.state for item in bundle.items}
    assert states.get("甲承诺明日归还钥匙") == "OPEN", states
    assert states.get("甲立誓三年后复仇") == "OPEN", (
        f"只给了人物名，却把有歧义的承诺关掉了：{states}"
    )


# ---------------------------------------------------------------------------
# C-05 终局判断的证据
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ending_verdict_prompt_carries_written_text_and_open_promises(novel_db):
    """终局判断必须看到已写出的正文与未兑现承诺，而不只是路径计划（C-05）。"""
    captured: dict[str, str] = {}

    class _CaptureProvider:
        model_name = "test-model"

        async def generate_structured(self, *, response_model, **kwargs):
            captured["user_prompt"] = str(kwargs.get("user_prompt") or "")

            if getattr(response_model, "__name__", "") == "EndingVerdict":
                return StructuredModelResponse(
                    output=response_model(
                        story_complete=False, reason="甲立誓复仇这条线还没收"
                    ),
                    usage=ModelUsage(10, 20),
                    model="test",
                )
            raise AssertionError(f"unexpected schema: {response_model}")

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        run = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=3,
            attempt=1,
            state=ChapterRunState.CANONIZED.value,
            content="正文",
        )
        s.add(run)
        await s.flush()
        verdict = await generation.generate_ending_verdict(
            _CaptureProvider(),  # type: ignore[arg-type]
            session=s,
            work=work,
            run=run,
            raw_intent="讲一个关于信任的故事",
            genre="悬疑",
            ending_statement="",
            volume_no=1,
            latest_chapter_no=3,
            completed_nodes=[{"title": "交付", "promise": "钥匙必须归还"}],
            accepted_text="他把钥匙放在桌上，转身走进雨里。",
            verified_facts=[{"statement": "甲归还了钥匙"}],
            open_promises=[{"content": "甲立誓三年后复仇"}],
        )

    assert verdict.story_complete is False
    payload = json.loads(captured["user_prompt"])
    # 计划性信息仍在
    assert payload["completed_nodes"], "路径节点应当仍在请求里"
    # 关键是这三项：终局判断要看「已经写出来的」，不是「计划要写的」
    assert "他把钥匙放在桌上" in payload["accepted_text"], payload["accepted_text"]
    assert "甲归还了钥匙" in payload["verified_facts"], payload["verified_facts"]
    assert "甲立誓三年后复仇" in payload["open_promises"], payload["open_promises"]
