"""连续三章：接受正文后的交接必须进入下一章，失效交接不得用更早章节填上。

正文是固定样例，不调用模型。走的是生产 ASSEMBLE 与最终接受。
"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from regent.novel.application import generation
from regent.novel.application.chapter_accept import finalize_chapter_acceptance
from regent.novel.application.chapter_review import _rewind_script_after_review
from regent.novel.domain.chapter_handoff import (
    continuation_issues,
    initial_entry_from_previous,
)
from regent.novel.domain.script_protocol import PROTOCOL_SCRIPT_SCENE, empty_script_state
from regent.novel.domain.states import ChapterRunState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    NovelPrincipalModel,
    StoryGoalModel,
    StoryWorkModel,
)

CH1 = "他靠在门后，左臂还在渗血，追兵的脚步停在门外。他没有推门。"
CH2 = "门被撞开。他仍按着渗血的左臂翻出侧窗，停在巷口，左臂已经包上布条。"


async def _work(session) -> StoryWorkModel:
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"handoff:{owner}"))
    work = StoryWorkModel(
        id=uuid.uuid4(),
        owner_id=owner,
        state="RUNNING",
        genre="悬疑",
    )
    session.add(work)
    await session.flush()
    return work


async def _goal_and_path(session, work: StoryWorkModel) -> None:
    session.add(
        StoryGoalModel(
            id=uuid.uuid4(),
            work_id=work.id,
            raw_intent="负伤后摆脱追兵",
            normalized_goal="伤势和追兵要有下文",
            version=1,
        )
    )
    path = CriticalPathModel(id=uuid.uuid4(), work_id=work.id, node_count=2)
    session.add(path)
    await session.flush()
    for ordinal, node_id in enumerate(("n1", "n2"), start=1):
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(),
                path_id=path.id,
                node_id=node_id,
                ordinal=ordinal,
                title=f"节点{ordinal}",
            )
        )


def _accept(run: ChapterRunModel, content: str, state: dict) -> None:
    digest = hashlib.sha256(content.encode()).hexdigest()
    run.content = content
    run.word_count = len(content)
    run.review = {"passed": True}
    run.generation_context = {
        **(run.generation_context or {}),
        "architecture_version": "director_script_scene",
    }
    finalize_chapter_acceptance(
        run,
        {
            "working_state": state,
            "accepted": [],
            "takes": [],
            "script_protocol": {},
            "review_history": [{"content_hash": digest, "passed": True}],
        },
        review_passed=True,
    )
    run.state = ChapterRunState.CANONIZED.value


def _queued(work: StoryWorkModel, chapter_no: int, attempt: int = 1) -> ChapterRunModel:
    return ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        attempt=attempt,
        state=ChapterRunState.QUEUED.value,
        generation_context={"architecture_version": "director_script_scene"},
    )


@pytest.mark.asyncio
async def test_three_chapters_keep_the_wound_and_drop_a_stale_exit(novel_db):
    async with novel_db() as session:
        work = await _work(session)
        await _goal_and_path(session, work)
        chapter1 = _queued(work, 1)
        session.add(chapter1)
        _accept(
            chapter1,
            CH1,
            {"wound": "left_arm_bleeding", "place": "behind_door", "action": "waiting"},
        )
        await session.commit()

        chapter2 = _queued(work, 2)
        session.add(chapter2)
        await session.commit()
        await generation.assemble(session, work=work, run=chapter2)

        handoff = chapter2.generation_context["previous_handoff"]
        assert chapter2.generation_context["previous_handoff_status"] == "bound"
        assert handoff["chapter_no"] == 1
        assert handoff["actual_state"]["wound"] == "left_arm_bleeding"
        opening, entries = initial_entry_from_previous(handoff)
        assert entries == [opening]
        assert "左臂还在渗血" in opening
        assert "追兵的脚步停在门外" in opening
        assert "客栈" not in opening
        assert continuation_issues(mode="direct", transition_note="", has_previous=True) == []
        assert continuation_issues(mode="time_jump", transition_note="", has_previous=True) == [
            "transition_note_required"
        ]
        assert continuation_issues(
            mode="time_jump",
            transition_note="次日他已从侧窗逃脱并包扎，才到达客栈",
            has_previous=True,
        ) == []

        _accept(
            chapter2,
            CH2,
            {"wound": "left_arm_bandaged", "place": "alley", "action": "escaped"},
        )
        await session.commit()

        recovered = _queued(work, 3)
        session.add(recovered)
        await session.commit()
        await generation.assemble(session, work=work, run=recovered)
        recovered_handoff = recovered.generation_context["previous_handoff"]
        assert recovered.generation_context["previous_handoff_status"] == "bound"
        assert recovered_handoff["chapter_no"] == 2
        assert recovered_handoff["actual_state"]["place"] == "alley"
        recovered_opening, _ = initial_entry_from_previous(recovered_handoff)
        assert "左臂已经包上布条" in recovered_opening
        assert "追兵的脚步停在门外" not in recovered_handoff["tail_excerpt"]

        chapter2.content = CH2 + "他已经在客栈里庆功。"
        await session.commit()
        stale = _queued(work, 3, attempt=2)
        session.add(stale)
        await session.commit()
        await generation.assemble(session, work=work, run=stale)
        assert stale.generation_context["previous_handoff"] is None
        assert stale.generation_context["previous_handoff_status"] == "stale"
        assert initial_entry_from_previous(stale.generation_context["previous_handoff"]) == (
            "",
            [""],
        )

        production = {
            "protocol": PROTOCOL_SCRIPT_SCENE,
            "phase": "DONE",
            "takes": [{"status": "ACCEPTED", "content": CH2}],
            "accepted": [0],
            "script_protocol": empty_script_state(),
        }
        production["script_protocol"]["production_packet"] = {"script": {"title": "保留"}}
        review_run = SimpleNamespace(
            content=CH2,
            review={"passed": True},
            generation_context={"production": production},
        )
        assert _rewind_script_after_review(
            review_run,
            production,
            issues=["同一事件在两场重复展开：再次发现追兵"],
            failed_scene_index=0,
            proto=PROTOCOL_SCRIPT_SCENE,
        ) is False
        assert production["phase"] == "COMPILE_SCENES"
        assert production["script_protocol"]["scene_texts"] == []
        assert review_run.review["passed"] is False
