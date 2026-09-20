"""PR-B：VALIDATE/ACCEPT 按 repair_locate 定位修订场，禁止默认末场。

行为断言，不复述分支常量。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.repair_locate import (
    apply_repair_target_to_script_state,
    locate_fail_messages,
    select_repair_target,
)
from regent.novel.domain.scene_card import BeatVerdict, SceneCard, StructuredBeat
from regent.novel.domain.script_protocol import (
    PROTOCOL_SCRIPT_SCENE,
    ChapterCreativeAccept,
    ChapterScript,
    ScriptChoice,
    empty_script_state,
)

PARA = "雨水沿窗棂流下，两个人仍旧没有开口，钥匙静静躺在桌面正中。"


def _dup_block(n: int = 3) -> str:
    return "\n\n".join([PARA] * n)


class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append({"schema": response_model, **kwargs})
        output = self.outputs.pop(0)
        assert isinstance(output, response_model), (output, response_model)
        return StructuredModelResponse(
            output=output.model_copy(deep=True), usage=ModelUsage(10, 20), model="test"
        )


class Session:
    async def scalar(self, query):
        return None

    async def scalars(self, query):
        return SimpleNamespace(all=lambda: [])

    def add(self, row):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: None, all=lambda: [])


def _card(scene_id: str, beat_id: str) -> SceneCard:
    return SceneCard(
        scene_id=scene_id,
        purpose="逼出选择",
        setting="摄影棚",
        entry_state="对峙",
        protagonist_objective="保住秘密",
        opposition="逼问",
        core_choice="交或不交",
        emotion_arc="紧到松",
        exit_change="把柄易手",
        beats=[StructuredBeat(beat_id=beat_id, text="当场表态", must_show=True)],
        target_chars=500,
    )


def _scene_run(*, phase: str, texts: list[str], scene_index: int | None = None):
    cards = [
        _card("s0", "b0").model_dump(mode="json"),
        _card("s1", "b1").model_dump(mode="json"),
        _card("s2", "b2").model_dump(mode="json"),
    ]
    packet = {
        "script": ChapterScript(
            title_line="选定",
            end_change="把柄易手",
            cost="暴露",
            cost_type="暴露",
            power_payoff="读出伪证",
            beats=["当场表态"],
        ).model_dump(mode="json"),
        "direction": ScriptChoice(
            selected_id="alpha",
            must_land_beats=["当场表态"],
        ).model_dump(mode="json"),
        "must_land_beats": ["当场表态"],
    }
    sp = empty_script_state()
    sp.update(
        {
            "selected_id": "alpha",
            "production_packet": packet,
            "choice": packet["direction"],
            "candidates": {"alpha": packet["script"]},
            "creative_repairs": 0,
            "scene_plan": {
                "cards": cards,
                "chapter_goal": "x",
            },
            "scene_index": scene_index if scene_index is not None else len(texts) - 1,
            "scene_texts": list(texts),
            "scene_state_trail": [{"s": i} for i in range(len(texts))],
            "working_state": {"s": len(texts) - 1},
            "chapter_validate_repairs": 0,
        }
    )
    chapter_text = "\n\n".join(texts)
    take = {
        "scene_index": 0,
        "take_no": 1,
        "content": chapter_text,
        "events": [],
        "status": "DRAFT",
        "scene_state": "DIRECTOR_VIEW",
        "artifact": "PROSE",
        "brief": {"purpose": "script_chapter"},
        "turn": 0,
        "performances": [],
        "round_actions": [],
        "revisions": 0,
        "prose_versions": [chapter_text],
        "state_before": {},
        "validation": {"passed": False, "issues": [], "facts": []},
    }
    production = {
        "schema_version": 1,
        "protocol": PROTOCOL_SCRIPT_SCENE,
        "phase": phase,
        "cast": {"主角": {}},
        "takes": [take],
        "accepted": [],
        "decisions": [],
        "working_state": {},
        "script_protocol": sp,
        "call_count": 0,
        "committed_minor": 0,
        "scene_index": 0,
        "plan": {"scenes": [{"purpose": "x", "actors": []}]},
    }
    run = SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        generation_context={
            "architecture_version": d.SCRIPT_SCENE_ARCHITECTURE,
            "executor": d.SCRIPT_SCENE_ARCHITECTURE,
            "executor_version": "director_script_scene@1",
            "canon": [],
            "actual_state": {},
            "recent_chapters": [],
            "memory": [],
            "production": production,
        },
        performances=[],
        review={},
        user_guidance={},
        current_step="PRODUCE",
        content=chapter_text,
        title="第1章",
        word_count=len(chapter_text),
        input_version=1,
    )
    work = SimpleNamespace(id=uuid.uuid4())
    return run, work


# --- domain：定位纯函数 ---


def test_locate_front_scene_duplicate_targets_first_scene():
    """前场重复：必须定位到 s0，不得默认末场。"""
    texts = [_dup_block(), "独有中场内容。" * 40, "独有末场内容。" * 40]
    cards = [_card("s0", "b0"), _card("s1", "b1"), _card("s2", "b2")]
    msg = (
        "[front:redundant] 同章出现完全相同段落重复；摘录「"
        + PARA
        + "」请去重合并场景"
    )
    located = locate_fail_messages([msg], scene_texts=texts, cards=cards)
    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改本场："
    )
    assert target.locatable
    assert target.scene_index == 0
    assert target.scene_id == "s0"
    sp = {
        "scene_texts": list(texts),
        "scene_state_trail": [{"i": 0}, {"i": 1}, {"i": 2}],
        "working_state": {"i": 2},
    }
    apply_repair_target_to_script_state(sp, target)
    assert sp["scene_index"] == 0
    assert len(sp["scene_texts"]) == 1
    assert sp["pending_repair_ticket"]["scene_id"] == "s0"


def test_locate_last_scene_duplicate_targets_last_scene():
    texts = ["独有开场。" * 40, "独有中场。" * 40, _dup_block()]
    cards = [_card("s0", "b0"), _card("s1", "b1"), _card("s2", "b2")]
    msg = "[front:redundant] 同章大段叙述几乎整段复读；摘录「" + PARA + "」"
    located = locate_fail_messages([msg], scene_texts=texts, cards=cards)
    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改本场："
    )
    assert target.locatable
    assert target.scene_index == 2
    assert target.scene_id == "s2"


def test_locate_cross_scene_duplicate_earliest():
    """跨场同一摘录：回退最早场，ticket 含多个 scene_id。"""
    texts = [
        "开场铺垫。" * 30 + "\n\n" + PARA,
        "中场推进。" * 30 + "\n\n" + PARA,
        "独有末场。" * 40,
    ]
    cards = [_card("s0", "b0"), _card("s1", "b1"), _card("s2", "b2")]
    msg = "[front:redundant] 同章出现完全相同段落重复；摘录「" + PARA + "」"
    located = locate_fail_messages([msg], scene_texts=texts, cards=cards)
    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改本场："
    )
    assert target.locatable
    assert target.scene_index == 0
    assert "s0" in target.scene_ids
    assert "s1" in target.scene_ids


def test_locate_unlocatable_not_default_last():
    texts = ["独有甲。" * 40, "独有乙。" * 40]
    cards = [_card("s0", "b0"), _card("s1", "b1")]
    located = locate_fail_messages(
        ["突破剧本终点"], scene_texts=texts, cards=cards
    )
    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改本场："
    )
    assert target.locatable is False
    assert "无法局部定位" in target.instruction


# --- produce_tick：VALIDATE / ACCEPT 接线 ---


@pytest.mark.asyncio
async def test_validate_locates_front_dup_not_last_scene():
    front = _dup_block()
    texts = [front, "独有中场。" * 40, "独有末场。" * 40]
    run, work = _scene_run(phase="VALIDATE", texts=texts)
    run.generation_context["production"]["takes"][0]["content"] = "\n\n".join(texts)
    fail = (
        "[front:redundant] 同章出现完全相同段落重复；摘录「"
        + PARA
        + "」请去重合并场景"
    )
    provider = Provider(
        [
            d.ScriptChapterValidation(
                hard_fails=[fail], soft_notes=[], facts=[], located_issues=[]
            )
        ]
    )
    ok = await d.produce_tick(Session(), provider=provider, work=work, run=run)
    assert ok is False
    prod = run.generation_context["production"]
    sp = prod["script_protocol"]
    assert prod["phase"] == "WRITE_SCENE"
    assert sp["scene_index"] == 0, f"必须定位前场，不得默认末场: {sp['scene_index']}"
    assert len(sp["scene_texts"]) == 1
    assert sp["scene_texts"][0] == front
    ticket = sp.get("pending_repair_ticket") or {}
    assert ticket.get("scene_id") == "s0"
    assert ticket.get("issue_ids")
    located = (prod["takes"][0].get("validation") or {}).get("located_issues") or []
    assert located, "validation 须落盘 located_issues"


@pytest.mark.asyncio
async def test_validate_unlocatable_stops_no_write_scene():
    texts = ["独有甲。" * 40, "独有乙。" * 40]
    run, work = _scene_run(phase="VALIDATE", texts=texts)
    provider = Provider(
        [
            d.ScriptChapterValidation(
                hard_fails=["突破剧本终点"], soft_notes=[], facts=[]
            )
        ]
    )
    with pytest.raises(ProductionStopped, match="无法局部定位") as ei:
        await d.produce_tick(Session(), provider=provider, work=work, run=run)
    assert "突破剧本终点" in str(ei.value)
    prod = run.generation_context["production"]
    assert prod["phase"] == "VALIDATE"
    assert prod["phase"] != "WRITE_SCENE"


@pytest.mark.asyncio
async def test_validate_empty_texts_still_hard_stop():
    """无分场正文时不得假修订，保持可诊断停机。"""
    texts = ["独有甲。" * 40, "独有乙。" * 40]
    run, work = _scene_run(phase="VALIDATE", texts=texts)
    run.generation_context["production"]["script_protocol"]["scene_texts"] = []
    provider = Provider(
        [
            d.ScriptChapterValidation(
                hard_fails=["突破剧本终点"], soft_notes=[], facts=[]
            )
        ]
    )
    with pytest.raises(ProductionStopped) as ei:
        await d.produce_tick(Session(), provider=provider, work=work, run=run)
    msg = str(ei.value)
    assert any(
        k in msg
        for k in ("分场臂不整章重写", "无法局部定位", "核验缺少正文")
    ), msg
    assert run.generation_context["production"]["phase"] != "WRITE_SCENE"


@pytest.mark.asyncio
async def test_accept_render_locates_from_validation_issues():
    texts = [_dup_block(), "独有中场。" * 40, "独有末场。" * 40]
    run, work = _scene_run(phase="ACCEPT_CHAPTER", texts=texts)
    fail = (
        "[front:redundant] 同章出现完全相同段落重复；摘录「"
        + PARA
        + "」请去重合并场景"
    )
    take = run.generation_context["production"]["takes"][0]
    take["content"] = "\n\n".join(texts)
    take["validation"] = {
        "passed": False,
        "issues": [fail],
        "facts": [
            {
                "statement": "钥匙在桌上",
                "quote": PARA[:20],
                "known_by": ["主角"],
            }
        ],
    }
    provider = Provider(
        [
            ChapterCreativeAccept(
                accept=False, fault="render", notes="存在整段复读，需去重"
            )
        ]
    )
    ok = await d.produce_tick(Session(), provider=provider, work=work, run=run)
    assert ok is False
    sp = run.generation_context["production"]["script_protocol"]
    assert run.generation_context["production"]["phase"] == "WRITE_SCENE"
    assert sp["scene_index"] == 0
    assert sp.get("pending_repair_ticket", {}).get("scene_id") == "s0"


@pytest.mark.asyncio
async def test_write_revision_ticket_mismatch_stops():
    """票据 scene_id 与当前卡不一致：必须停机，不得改错场。"""
    texts = ["独有甲。" * 40, "独有乙。" * 40]
    run, work = _scene_run(phase="WRITE_SCENE", texts=texts, scene_index=1)
    sp = run.generation_context["production"]["script_protocol"]
    sp["scene_revision_instruction"] = "章节核验未过，请改本场：xxx"
    sp["pending_repair_ticket"] = {
        "scene_index": 1,
        "scene_id": "s0",  # 与 cards[1]=s1 冲突
        "issue_ids": ["front:redundant#x"],
        "issues": [],
        "base_content_hash": "",
        "must_remove_quotes": [],
        "verify_rules": [],
    }

    class _SceneProse:
        def __init__(self):
            self.content = "新稿。" * 50
            self.beat_evidence = {}

    # 不应走到模型：票据校验在 call 之前
    provider = Provider([])
    with pytest.raises(ProductionStopped, match="scene_id|不一致"):
        await d.produce_tick(Session(), provider=provider, work=work, run=run)
    assert provider.requests == []


@pytest.mark.asyncio
async def test_auditscene_uses_index_not_list_tail():
    """AUDIT 读 texts[idx]；index < 卡数但 >= texts 长度时停机。"""
    texts = ["独有甲。" * 40, "独有乙。" * 40]
    run, work = _scene_run(phase="AUDIT_SCENE", texts=texts, scene_index=1)
    sp = run.generation_context["production"]["script_protocol"]
    sp["pending_scene_beat_evidence"] = {"b2": "他把钥匙放在桌上。"}
    # cards 有 3 张，texts 只有 2 段 → idx=2 越界 texts
    sp["scene_index"] = 2
    provider = Provider([])
    with pytest.raises(ProductionStopped, match="AUDIT目标场"):
        await d.produce_tick(Session(), provider=provider, work=work, run=run)


def test_locate_mid_scene_duplicate_targets_middle():
    """纯中场重复：必须定位到 s1，不得修首场或末场。"""
    texts = [
        "独有开场内容。" * 40,
        _dup_block(),
        "独有末场内容。" * 40,
    ]
    cards = [_card("s0", "b0"), _card("s1", "b1"), _card("s2", "b2")]
    msg = "[front:redundant] 同章出现完全相同段落重复；摘录「" + PARA + "」"
    located = locate_fail_messages([msg], scene_texts=texts, cards=cards)
    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改本场："
    )
    assert target.locatable
    assert target.scene_index == 1
    assert target.scene_id == "s1"
    sp = {
        "scene_texts": list(texts),
        "scene_state_trail": [{"i": 0}, {"i": 1}, {"i": 2}],
        "working_state": {"i": 2},
    }
    apply_repair_target_to_script_state(sp, target)
    assert len(sp["scene_texts"]) == 2
    assert sp["scene_texts"][-1] == texts[1]
    assert "s0" not in (sp.get("pending_repair_ticket") or {}).get("scene_id", "")


def test_verify_repair_progress_by_code_not_issue_id_wording():
    """文案变了但 code 相同仍视为未解决；code 消失且摘录不再双份 → progress。"""
    from regent.novel.domain.repair_locate import verify_repair_progress

    ticket = {
        "issue_ids": ["front:redundant#abcdef123456"],
        "codes": ["front:redundant"],
        "must_remove_quotes": [PARA],
        "base_content_hash": "x",
    }
    chapter = "\n\n".join([PARA, "独有。" * 20, PARA])
    # 仍有 redundant，措辞不同
    still = verify_repair_progress(
        ticket=ticket,
        chapter_content=chapter,
        remaining_fails=["[front:redundant] 另一句表述；摘录「" + PARA[:20] + "」"],
    )
    assert still["progress"] is False
    assert "front:redundant" in still["open_codes"]

    fixed_chapter = "\n\n".join([PARA, "独有。" * 20])
    done = verify_repair_progress(
        ticket=ticket,
        chapter_content=fixed_chapter,
        remaining_fails=[],
    )
    assert done["progress"] is True


def test_format_revision_instruction_includes_action_and_quotes():
    from regent.novel.domain.repair_locate import (
        LocatedIssue,
        format_revision_instruction,
    )

    issue = LocatedIssue(
        issue_id="front:redundant#x",
        code="front:redundant",
        scene_ids=("s0", "s1"),
        evidence_quotes=(PARA,),
        expected_action="remove_dup",
        message="[front:redundant] 复读",
    )
    text = format_revision_instruction(
        prefix="章节核验未过，请改本场",
        issues=[issue],
        scene_ids=("s0", "s1"),
    )
    assert "remove_dup" in text
    assert "涉及场" in text
    assert PARA[:20] in text
    assert "必须删除" in text or "不得原样" in text


def test_review_rewind_uses_locate_not_default_last():
    """审校回退：有摘录时按 locate 到前场，即使 failed_scene_index 指向末场。"""
    from regent.novel.application.chapter_review import _rewind_script_after_review
    from regent.novel.domain.script_protocol import PROTOCOL_SCRIPT_SCENE

    texts = [_dup_block(), "独有中场。" * 40, "独有末场。" * 40]
    cards = [
        _card("s0", "b0").model_dump(mode="json"),
        _card("s1", "b1").model_dump(mode="json"),
        _card("s2", "b2").model_dump(mode="json"),
    ]
    sp = empty_script_state()
    sp.update(
        {
            "scene_plan": {"cards": cards, "chapter_goal": "x"},
            "scene_texts": list(texts),
            "scene_state_trail": [{"i": 0}, {"i": 1}, {"i": 2}],
            "working_state": {"i": 2},
            "scene_index": 2,
            "chapter_validate_repairs": 0,
        }
    )
    production = {
        "protocol": PROTOCOL_SCRIPT_SCENE,
        "phase": "DONE",
        "takes": [{"status": "ACCEPTED", "content": "\n\n".join(texts)}],
        "accepted": [0],
        "script_protocol": sp,
        "working_state": {},
    }
    run = SimpleNamespace(
        content="\n\n".join(texts),
        review={},
        generation_context={"production": production},
    )
    fail = "[front:redundant] 同章出现完全相同段落重复；摘录「" + PARA + "」"
    ok = _rewind_script_after_review(
        run,
        production,
        issues=[fail],
        failed_scene_index=2,  # 模型误指末场
        proto=PROTOCOL_SCRIPT_SCENE,
    )
    assert ok is False
    assert production["phase"] == "WRITE_SCENE"
    assert sp["scene_index"] == 0
    assert sp.get("pending_repair_ticket", {}).get("scene_id") == "s0"
    assert len(sp["scene_texts"]) == 1
