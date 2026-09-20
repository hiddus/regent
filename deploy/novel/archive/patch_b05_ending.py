"""同步 B-05：完结不再是「扩卷失败的副产物」，而是先按终局意图决定。

旧测试依赖两件被本轮修掉的行为：
1. 扩卷失败时套用「变强／更强大的对手」静态模板；
2. 末节点完成一律先扩卷，扩不出才结束（正常故事永不结束）。

改法是给 fixture 显式提供「用户终局」与「导演终局判断」，让每条路径都能从
正常生产入口走到结论，不再靠替换 expand_next_volume 制造终态。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
path = ROOT / "tests/unit/novel/test_last_node_and_volume.py"
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str, count: int = 1) -> None:
    global text
    got = text.count(old)
    assert got == count, f"old 出现 {got} 次（期望 {count}）-> {old[:70]!r}"
    text = text.replace(old, new)


sub(
    """from regent.novel.application.generation import StoryOutline
from regent.novel.domain.errors import Conflict""",
    """from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application.generation import StoryOutline, StoryOutlineNode
from regent.novel.domain.errors import Conflict""",
)

sub(
    """class _Provider(Provider):
    \"\"\"扩卷大纲走静态模板回退：这条用例只验证扩卷确实发生在 v2 成章分支。

    不能让扩卷吃掉一章的模型输出——输出被吞会让后续步骤报成 CALL_UNKNOWN，
    把「扩卷没发生」伪装成「模型调用不稳定」。
    \"\"\"

    async def generate_structured(self, *, response_model, **kwargs):
        if response_model is StoryOutline:
            raise RuntimeError("fixture has no volume outline")
        return await super().generate_structured(response_model=response_model, **kwargs)""",
    '''def _volume_outline(volume_title="第二卷") -> StoryOutline:
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
        return await super().generate_structured(response_model=response_model, **kwargs)''',
)

sub(
    """async def _boot(sessions, *, node_count: int = 2, end_chapter: int = 6):
    \"\"\"一部有一卷、若干节点的作品；节点数与卷范围都是显式给出的。\"\"\"
    owner, work_id = uuid.uuid4(), uuid.uuid4()""",
    """async def _boot(
    sessions, *, node_count: int = 2, end_chapter: int = 6, ending_target_volume: int = 0
):
    \"\"\"一部有一卷、若干节点的作品；节点数与卷范围都是显式给出的。

    ``ending_target_volume`` 是用户认可的终局：它是完结判定的第一依据，给了它
    就不再问模型——问模型等于把用户的数字降级成一个建议（B-05）。
    \"\"\"
    owner, work_id = uuid.uuid4(), uuid.uuid4()""",
)

sub(
    """                state="READY",
                genre="悬疑",
                total_volume_count=1,
            )""",
    """                state="READY",
                genre="悬疑",
                total_volume_count=1,
                ending_target_volume=ending_target_volume,
            )""",
)

sub(
    """async def _noop_event(session, **kwargs):
    return None""",
    """async def _noop_event(session, **kwargs):
    return None


def _capture_events(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    async def _fake(session, **kwargs):
        captured.append(kwargs)
        return None

    monkeypatch.setattr(works, "append_event", _fake)
    return captured""",
)

# --- 用例 1：末节点完成且用户还要下一卷 → 扩卷（走真实大纲，不套模板） ---------
sub(
    """    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(
        _chapter_outputs(True) + _chapter_outputs(True) + _chapter_outputs(False)
    )
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6)""",
    """    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(
        _chapter_outputs(True) + _chapter_outputs(True) + _chapter_outputs(False),
        outline=_volume_outline(),
    )
    # 用户要写两卷：第一卷末节点完成后应当继续扩卷
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6, ending_target_volume=2)""",
)

# --- 用例 2：完结必须是判出来的，不是扩卷失败的副产物 -------------------------
sub(
    '''async def test_story_ends_when_no_new_node_can_be_expanded(novel_db, monkeypatch):
    """扩不出新节点时整本结束：start_run 不得再开一个没有目标节点的章。"""
    monkeypatch.setattr(works, "append_event", _noop_event)

    async def _no_expansion(session, *, work, provider=None):
        return None

    monkeypatch.setattr(works, "expand_next_volume", _no_expansion)
    provider = _Provider(_chapter_outputs(True) + _chapter_outputs(True))
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6)''',
    '''async def test_story_ends_when_user_target_volume_is_reached(novel_db, monkeypatch):
    """用户说写几卷就写几卷：最后一卷写完即结束，不必先试扩卷再失败。

    这条用例刻意**不**替换 expand_next_volume：完结必须是从正常生产入口判出来的，
    而不是把扩卷函数换成恒返回 None 之后也能 DONE（B-05）。
    """
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(_chapter_outputs(True) + _chapter_outputs(True))
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6, ending_target_volume=1)''',
)

text += '''

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
'''

path.write_text(text, encoding="utf-8")
print("patched test_last_node_and_volume.py")
