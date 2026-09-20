"""职责删减：场景协议、预算 resume、记忆槽与状态软属性。"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import direction as d
from regent.novel.application import works
from regent.novel.domain.errors import InvalidState
from regent.novel.domain.states import StoryWorkState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    NovelPrincipalModel,
    StoryWorkModel,
)


def test_soft_literary_attributes_skip_hard_evidence():
    from regent.novel.domain.scene_requirements import freeze_scene_requirements

    reqs = freeze_scene_requirements(
        [
            {
                "statement": "他皱了皱眉",
                "reader_visible": True,
                "state_changes": {"主角.情绪": "不安", "钥匙.归属": "桌上"},
            }
        ]
    )
    by_key = {r["state_key"]: r for r in reqs}
    assert by_key["主角.情绪"]["require_direct_evidence"] is False
    assert by_key["钥匙.归属"]["require_direct_evidence"] is True


def test_memory_slots_group_and_project_boundaries():
    from regent.novel.domain import memory as domain

    payloads = [
        {"kind": "rule", "subject": "规矩", "content": "钥匙只能交给掌灯人", "entities": []},
        {"kind": "belief", "subject": "甲", "content": "甲以为乙已走", "entities": ["甲"]},
        {
            "kind": "promise",
            "subject": "甲",
            "content": "甲承诺归还",
            "entities": ["甲"],
            "state": "OPEN",
        },
        {"kind": "director_note", "subject": "待办", "content": "勿泄露", "entities": []},
        {
            "kind": "reader_knowledge",
            "subject": "读者",
            "content": "读者已知背叛",
            "entities": [],
        },
    ]
    character = domain.project_payloads(payloads, "character", "甲")
    narrator = domain.project_payloads(payloads, "narrator")
    grouped = domain.group_by_slot(character)
    assert all(p.get("slot") for p in character)
    assert {p["kind"] for p in character} == {"rule", "belief", "promise"}
    assert "director_note" not in {p["kind"] for p in narrator}
    assert "reader_knowledge" not in {p["kind"] for p in character}
    assert grouped["fact"] and grouped["known"] and grouped["promise"]
    assert grouped["summary"] == []


def test_new_take_defaults_to_scene_protocol():
    production = {
        "scene_index": 0,
        "takes": [],
        "working_state": {},
        "protocol": d.PROTOCOL_SCENE,
    }
    d._new_take(production, {"purpose": "开场", "actors": []})
    assert production["phase"] == "SCENE"


def test_new_take_beat_protocol_starts_at_act():
    production = {
        "scene_index": 0,
        "takes": [],
        "working_state": {},
        "protocol": d.PROTOCOL_BEAT,
    }
    d._new_take(production, {"purpose": "开场", "actors": []})
    assert production["phase"] == "ACT"


def test_stable_executor_is_scene_protocol_v2():
    from regent.novel.application import executor as executor_app

    assert executor_app.executor_version(d.ARCHITECTURE) == "director_v2@2"
    assert d.production_protocol(
        type(
            "R",
            (),
            {"generation_context": {"architecture_version": d.ARCHITECTURE}},
        )()
    ) == d.PROTOCOL_SCENE


@pytest.mark.asyncio
async def test_resume_rejects_budget_pause_without_authorize(novel_db):
    async with novel_db() as session:
        owner = uuid.uuid4()
        work_id = uuid.uuid4()
        session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
        work = StoryWorkModel(
            id=work_id,
            owner_id=owner,
            state=StoryWorkState.PAUSED_QUOTA.value,
            genre="悬疑",
            latest_chapter_no=1,
        )
        session.add(work)
        await session.flush()
        session.add(
            ChapterRunModel(
                id=uuid.uuid4(),
                work_id=work_id,
                branch_id=work.branch_id,
                chapter_no=1,
                attempt=1,
                state="RUNNING",
                generation_context={
                    "budget_pause": {"kind": "calls", "call_count": 120},
                    "production": {"call_count": 120},
                },
            )
        )
        await session.flush()
        with pytest.raises(InvalidState, match="authorize_budget"):
            await works.resume_work(session, owner_id=owner, work_id=work_id)


@pytest.mark.asyncio
async def test_user_pause_without_budget_marker_can_resume(novel_db):
    async with novel_db() as session:
        owner = uuid.uuid4()
        work_id = uuid.uuid4()
        session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
        work = StoryWorkModel(
            id=work_id,
            owner_id=owner,
            state=StoryWorkState.PAUSED_COST.value,
            genre="悬疑",
            latest_chapter_no=1,
        )
        session.add(work)
        await session.flush()
        session.add(
            ChapterRunModel(
                id=uuid.uuid4(),
                work_id=work_id,
                branch_id=work.branch_id,
                chapter_no=1,
                attempt=1,
                state="RUNNING",
                generation_context={"production": {"call_count": 10}},
            )
        )
        await session.flush()
        state = await works.resume_work(session, owner_id=owner, work_id=work_id)
        state_val = getattr(state, "state", state)
        assert getattr(state_val, "value", state_val) == StoryWorkState.RUNNING.value
