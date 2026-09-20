"""真实行为校验：调用 produce_tick / run_protocol_x，不复述 if/else。"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application import generation as gen
from regent.novel.application.editor_audit import EditorAuditResult
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.scene_card import BeatVerdict, SceneCard, StructuredBeat
from regent.novel.domain.script_protocol import (
    PROTOCOL_SCRIPT_SCENE,
    ChapterCreativeAccept,
    ChapterScript,
    ScriptChoice,
    empty_script_state,
)
from regent.novel.experiments import quality_ab as qa


TEXT = "他把钥匙放在桌上。" + "雨水沿窗棂流下，两个人仍旧没有开口。" * 65


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


def _card(scene_id: str = "s1", beat_id: str = "b1") -> SceneCard:
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
        beats=[
            StructuredBeat(
                beat_id=beat_id,
                text="当场表态",
                must_show=True,
            )
        ],
        target_chars=500,
    )


def _base_run(*, phase: str, sp_extra: dict | None = None, take_content: str = TEXT):
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
            "candidates": {"alpha": packet["script"], "beta": packet["script"]},
            "creative_repairs": 0,
        }
    )
    if sp_extra:
        sp.update(sp_extra)
    take = {
        "scene_index": 0,
        "take_no": 1,
        "content": take_content,
        "events": [],
        "status": "DRAFT",
        "scene_state": "DIRECTOR_VIEW",
        "artifact": "PROSE",
        "brief": {"purpose": "script_chapter"},
        "turn": 0,
        "performances": [],
        "round_actions": [],
        "revisions": 0,
        "prose_versions": [take_content],
        "state_before": {},
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
        content="",
        title="第1章",
        word_count=0,
        input_version=1,
    )
    work = SimpleNamespace(id=uuid.uuid4())
    return run, work


@pytest.mark.asyncio
async def test_real_validate_chapter_fail_stops_not_write_chapter():
    """章节核验硬失败：真实 produce_tick 必须停机，不得进入 WRITE_CHAPTER。"""
    run, work = _base_run(phase="VALIDATE")
    provider = Provider(
        [
            d.ScriptChapterValidation(
                hard_fails=["突破剧本终点"],
                soft_notes=[],
                facts=[],
            )
        ]
    )
    with pytest.raises(ProductionStopped, match="分场臂不整章重写") as ei:
        await d.produce_tick(Session(), provider=provider, work=work, run=run)
    assert "突破剧本终点" in str(ei.value)
    production = run.generation_context["production"]
    phase = production["phase"]
    assert phase == "VALIDATE", f"停机后不得改写为 WRITE_CHAPTER，实际={phase}"
    assert phase != "WRITE_CHAPTER"
    # 核验结果须落盘，不能只抛异常丢状态
    issues = (production["takes"][0].get("validation") or {}).get("issues") or []
    assert "突破剧本终点" in issues


@pytest.mark.asyncio
async def test_real_accept_render_stops_not_write_chapter():
    """创作验收 render 失败：真实 produce_tick 停机，不得 WRITE_CHAPTER。"""
    run, work = _base_run(phase="ACCEPT_CHAPTER")
    take = run.generation_context["production"]["takes"][0]
    take["validation"] = {
        "passed": True,
        "issues": [],
        "facts": [
            {
                "statement": "钥匙放在桌上",
                "quote": "他把钥匙放在桌上。",
                "known_by": ["主角"],
            }
        ],
    }
    provider = Provider(
        [
            ChapterCreativeAccept(
                accept=False, fault="render", notes="金手指只旁白没兑现"
            )
        ]
    )
    with pytest.raises(ProductionStopped, match="分场臂不整章重写"):
        await d.produce_tick(Session(), provider=provider, work=work, run=run)
    phase = run.generation_context["production"]["phase"]
    assert phase == "ACCEPT_CHAPTER"
    assert phase != "WRITE_CHAPTER"


@pytest.mark.asyncio
async def test_real_write_chapter_phase_rejected_by_whitelist():
    """即便状态被污染成 WRITE_CHAPTER，script_scene 白名单也必须拒绝。"""
    run, work = _base_run(phase="WRITE_CHAPTER")
    with pytest.raises(ProductionStopped, match="未知剧本协议阶段"):
        await d.produce_tick(Session(), provider=Provider([]), work=work, run=run)


@pytest.mark.asyncio
async def test_real_audit_exhausted_stops_without_advancing_scene():
    """本场修满仍硬失败：真实 AUDIT_SCENE 必须停机，scene_index 不得 +1。"""
    card = _card("s1", "b1")
    card2 = _card("s2", "b2")
    run, work = _base_run(
        phase="AUDIT_SCENE",
        sp_extra={
            "scene_plan": {
                "cards": [card.model_dump(mode="json"), card2.model_dump(mode="json")],
                "chapter_goal": "把柄易手",
            },
            "scene_index": 0,
            "scene_texts": [TEXT],
            "scene_repairs:s1": 2,  # 已用尽本场重写额度（MAX_SCENE_REPAIRS=2）
            "pending_scene_beat_evidence": {"b1": "他把钥匙放在桌上。"},
            "working_state": {"before": "ok"},
            "working_summary": "上场",
        },
    )
    provider = Provider(
        [
            d._SceneAudit(
                scene_id="s1",
                beat_verdicts=[
                    BeatVerdict(beat_id="b1", landed=False, evidence="", note="缺失")
                ],
                continuity_ok=True,
                hard_fails=["改因果终点"],
                facts=[],
                state_changes={"poison": "should_not_merge"},
            )
        ]
    )
    with pytest.raises(ProductionStopped, match="拒绝继续拍后续场") as ei:
        await d.produce_tick(Session(), provider=provider, work=work, run=run)
    assert "改因果终点" in str(ei.value)
    sp = run.generation_context["production"]["script_protocol"]
    assert sp["scene_index"] == 0, "失败场不得推进 scene_index"
    assert "poison" not in (sp.get("working_state") or {}), "失败场不得污染 working_state"
    fails = sp.get("scene_hard_fails") or []
    assert fails, "停机前必须落盘 scene_hard_fails"
    assert any("改因果终点" in x for x in fails)
    assert run.generation_context["production"]["phase"] == "AUDIT_SCENE"


@pytest.mark.asyncio
async def test_real_plan_pins_script_scene_protocol():
    """DIRECT 规划：architecture=script_scene 时 production.protocol 必须钉死 script_scene。"""
    from regent.novel.application.generation import execute_step
    from regent.novel.domain.states import ChapterStep

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
        },
        performances=[],
        review={},
        user_guidance={},
        current_step="DIRECT",
        content="",
        title="",
        word_count=0,
        input_version=1,
    )
    work = SimpleNamespace(id=uuid.uuid4())

    class PlanSession(Session):
        async def scalars(self, query):
            return SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        name="主角",
                        identity={},
                        drives={},
                        voice={"style": "克制"},
                    )
                ]
            )

    await execute_step(
        PlanSession(), provider=Provider([]), work=work, run=run, step=ChapterStep.DIRECT
    )
    production = run.generation_context["production"]
    assert production["protocol"] == PROTOCOL_SCRIPT_SCENE
    assert production["phase"] == "BRIEF"
    assert "逐场" in production["plan"]["reader_intent"]


def test_canon_facts_source_includes_script_scene():
    """_canon_facts 必须把 director_script_scene 纳入验收正文过滤集合。"""
    src = inspect.getsource(gen._canon_facts)
    assert "director_script_scene" in src
    assert "director_script" in src


@pytest.mark.asyncio
async def test_real_protocol_x_aborts_later_scenes_on_hard_fail(monkeypatch):
    """实验 X：本场场记硬失败后 steps 不得再出现下一场 write_。"""
    from regent.novel.experiments import scene_exec as sx
    from regent.novel.domain.script_protocol import assemble_production_packet

    selected = ChapterScript(
        title_line="甲",
        end_change="变",
        beats=["选择", "代价"],
        cost="暴露",
        cost_type="暴露",
        power_payoff="兑现",
        turning_point="当场",
    )
    choice = ScriptChoice(
        selected_id="alpha",
        must_land_beats=["选择", "代价"],
    )
    packet = assemble_production_packet(
        selected=selected,
        choice=choice,
        meta={"fragment_id": "f1_appraisal", "protocol": PROTOCOL_SCRIPT_SCENE},
    )

    plan = sx.ScenePlanDraft(
        cards=[_card("s1", "b1"), _card("s2", "b2")],
        chapter_goal="变",
    )

    async def fake_audit(*args, **kwargs):
        return sx.SceneAudit(
            scene_id="s1",
            beat_verdicts=[BeatVerdict(beat_id="b1", landed=False, note="缺")],
            continuity_ok=True,
            hard_fails=["改因果终点"],
            facts=[],
            state_changes={"poison": "x"},
        )

    long_prose = "他抬眼看向镜头，掌心发烫。" * 20

    class XProvider(Provider):
        async def generate_structured(self, *, response_model, **kwargs):
            name = response_model.__name__
            if name == "ScenePlanDraft":
                return StructuredModelResponse(
                    output=plan.model_copy(deep=True),
                    usage=ModelUsage(1, 1),
                    model="test",
                )
            if name == "SceneProseWithBeats":
                out = sx.SceneProseWithBeats(
                    content=long_prose,
                    beat_evidence={"b1": "他抬眼看向镜头"},
                )
                return StructuredModelResponse(
                    output=out, usage=ModelUsage(1, 1), model="test"
                )
            raise AssertionError(f"unexpected schema {name}")

    monkeypatch.setattr(sx, "_audit_scene", fake_audit)
    monkeypatch.setattr(sx, "validate_scene_plan", lambda *a, **k: [])
    meter = qa.BudgetMeter(call_cap=40)
    provider = qa.MeteredProvider(XProvider([]), meter)  # type: ignore[arg-type]
    frag = qa.fragment_by_id("f1_appraisal")
    result = await sx.run_protocol_x(
        provider, frag, max_revisions=0, fixed_packet=packet
    )
    assert str(result.stop_reason).startswith("scene_hard_fail"), result.stop_reason
    assert "write_s1" in result.steps_log
    assert "write_s2" not in result.steps_log, result.steps_log
    assert result.completed is False


@pytest.mark.asyncio
async def test_script_call_command_id_differs_by_scene():
    """不同场次 WRITE_SCENE/AUDIT_SCENE 不得共享同一 logical_call_id。"""
    card = _card("s1", "b1")
    card2 = _card("s2", "b2")
    write_ids = []
    audit_ids = []
    for scene_idx, texts in ((0, []), (0, [TEXT]), (1, [TEXT]), (1, [TEXT, TEXT])):
        sp = empty_script_state()
        sp.update(
            {
                "scene_plan": {
                    "cards": [card.model_dump(mode="json"), card2.model_dump(mode="json")],
                    "chapter_goal": "x",
                },
                "scene_index": scene_idx,
                "scene_texts": list(texts),
                "creative_repairs": 0,
            }
        )
        production = {"decisions": []}
        run = SimpleNamespace(input_version=1)
        write_ids.append(d._script_scene_command_id(run, production, sp, "WRITE_SCENE"))
        audit_ids.append(d._script_scene_command_id(run, production, sp, "AUDIT_SCENE"))
    # 同场不同草稿深度：恢复同一逻辑调用应稳定；换场必须换 id
    assert write_ids[0] == write_ids[1], "同场恢复应复用 logical_call_id"
    assert write_ids[2] == write_ids[3]
    assert write_ids[0] != write_ids[2], write_ids
    assert audit_ids[0] != audit_ids[2]
    assert write_ids[0] != audit_ids[0]
    assert any("sc0:s1" in x for x in write_ids)
    assert any("sc1:s2" in x for x in write_ids)


@pytest.mark.asyncio
async def test_real_audit_continuity_fail_blocks_and_rewrites():
    """连续性失败必须阻断放行，不得只记 soft note 后推进。"""
    card = _card("s1", "b1")
    run, work = _base_run(
        phase="AUDIT_SCENE",
        sp_extra={
            "scene_plan": {
                "cards": [card.model_dump(mode="json")],
                "chapter_goal": "x",
            },
            "scene_index": 0,
            "scene_texts": [TEXT],
            "pending_scene_beat_evidence": {"b1": "他把钥匙放在桌上。"},
            "working_state": {"before": "ok"},
            "working_summary": "上场",
        },
    )
    provider = Provider(
        [
            d._SceneAudit(
                scene_id="s1",
                beat_verdicts=[
                    BeatVerdict(
                        beat_id="b1",
                        landed=True,
                        evidence="他把钥匙放在桌上。",
                        note="",
                    )
                ],
                continuity_ok=False,
                continuity_issues=["与入场状态矛盾"],
                hard_fails=[],
                state_changes={"poison": "nope"},
            )
        ]
    )
    await d.produce_tick(Session(), provider=provider, work=work, run=run)
    production = run.generation_context["production"]
    assert production["phase"] == "WRITE_SCENE"
    sp = production["script_protocol"]
    assert int(sp.get("scene_repairs:s1") or 0) == 1
    assert "poison" not in (sp.get("working_state") or {})
    assert "连续性" in (sp.get("scene_revision_instruction") or "")


@pytest.mark.asyncio
async def test_real_audit_rejects_ungrounded_evidence():
    """landed 但证据不在正文：不得放行。"""
    card = _card("s1", "b1")
    run, work = _base_run(
        phase="AUDIT_SCENE",
        sp_extra={
            "scene_plan": {
                "cards": [card.model_dump(mode="json")],
                "chapter_goal": "x",
            },
            "scene_index": 0,
            "scene_texts": [TEXT],
            "pending_scene_beat_evidence": {},
            "working_state": {},
            "working_summary": "",
            "scene_repairs:s1": 2,  # 已用尽重写额度 → 停机
        },
    )
    provider = Provider(
        [
            d._SceneAudit(
                scene_id="s1",
                beat_verdicts=[
                    BeatVerdict(
                        beat_id="b1",
                        landed=True,
                        evidence="这段摘录根本不在正文里",
                        note="",
                    )
                ],
                continuity_ok=True,
                hard_fails=[],
            )
        ]
    )
    with pytest.raises(ProductionStopped, match="拒绝继续拍后续场"):
        await d.produce_tick(Session(), provider=provider, work=work, run=run)
    fails = run.generation_context["production"]["script_protocol"].get(
        "scene_hard_fails"
    )
    assert fails and any("evidence_ungrounded" in x for x in fails)


@pytest.mark.asyncio
async def test_validate_chapter_script_scene_rewinds_write_scene_not_watch_prose():
    """script_scene 整章审校失败：必须回到 WRITE_SCENE，不得 WATCH_PROSE。"""
    from regent.novel.domain.states import ChapterStep

    card = _card("s1", "b1")
    card2 = _card("s2", "b2")
    run, work = _base_run(phase="DONE", take_content=TEXT)
    production = run.generation_context["production"]
    sp = production["script_protocol"]
    sp.update(
        {
            "scene_plan": {
                "cards": [card.model_dump(mode="json"), card2.model_dump(mode="json")],
                "chapter_goal": "x",
            },
            "scene_texts": [TEXT, TEXT],
            "working_state": {"after_s1": "1", "after_s2": "2"},
            "scene_state_trail": [{}, {"after_s1": "1"}, {"after_s1": "1", "after_s2": "2"}],
            "scene_index": 2,
        }
    )
    production["accepted"] = [0, 1]
    production["takes"] = [
        {
            "scene_index": 0,
            "take_no": 1,
            "content": TEXT,
            "events": [],
            "status": "ACCEPTED",
            "scene_state": "ACCEPTED",
            "artifact": "PROSE",
            "brief": {"purpose": "x"},
            "turn": 0,
            "performances": [],
            "round_actions": [],
            "revisions": 0,
            "prose_versions": [TEXT],
            "state_before": {},
        },
        {
            "scene_index": 1,
            "take_no": 1,
            "content": TEXT,
            "events": [],
            "status": "ACCEPTED",
            "scene_state": "ACCEPTED",
            "artifact": "PROSE",
            "brief": {"purpose": "y"},
            "turn": 0,
            "performances": [],
            "round_actions": [],
            "revisions": 0,
            "prose_versions": [TEXT],
            "state_before": {"after_s1": "1"},
        },
    ]
    run.content = TEXT
    run.generation_context["production"] = production
    provider = Provider(
        [
            d.ChapterValidation(
                passed=False,
                issues=["第二场因果断裂"],
                failed_scene_index=1,
                node_completed=False,
                completion_quote="",
            ),
            EditorAuditResult(passed=True, issues=[]),
        ]
    )
    ok = await d.validate_chapter(Session(), provider=provider, work=work, run=run)
    assert ok is False
    prod = run.generation_context["production"]
    assert prod["phase"] == "WRITE_SCENE", prod["phase"]
    assert prod["phase"] != "WATCH_PROSE"
    sp2 = prod["script_protocol"]
    assert sp2["scene_index"] == 1
    # 保留失败场正文供修订；只丢掉其后场
    assert len(sp2["scene_texts"]) == 2
    assert sp2["working_state"] == {"after_s1": "1"}
    assert int(sp2.get("chapter_validate_repairs") or 0) >= 1


@pytest.mark.asyncio
async def test_validate_chapter_script_scene_scene_index_not_bound_to_accepted():
    """script_scene accepted 仅 1 个组装 take 时，failed_scene_index=1 不得误杀。"""
    from regent.novel.application.editor_audit import EditorAuditResult

    card = _card("s1", "b1")
    card2 = _card("s2", "b2")
    run, work = _base_run(phase="DONE", take_content=TEXT)
    production = run.generation_context["production"]
    production["protocol"] = "script_scene"
    sp = production["script_protocol"]
    sp.update(
        {
            "scene_plan": {
                "cards": [card.model_dump(mode="json"), card2.model_dump(mode="json")],
                "chapter_goal": "x",
            },
            "scene_texts": [TEXT, TEXT],
            "working_state": {"after_s1": "1"},
            "scene_state_trail": [{}, {"after_s1": "1"}, {"after_s1": "1", "after_s2": "2"}],
            "scene_index": 2,
            "creative_accept": {"accept": True, "fault": "ok", "notes": "ok"},
        }
    )
    # 真实分场臂：accepted 只有整章 take
    production["accepted"] = [0]
    production["takes"] = [
        {
            "scene_index": 0,
            "take_no": 1,
            "content": TEXT,
            "events": [],
            "status": "ACCEPTED",
            "scene_state": "ACCEPTED",
            "artifact": "PROSE",
            "brief": {"purpose": "chapter"},
            "turn": 0,
            "performances": [],
            "round_actions": [],
            "revisions": 0,
            "prose_versions": [TEXT],
            "state_before": {},
        }
    ]
    run.content = TEXT
    run.generation_context["production"] = production
    provider = Provider(
        [
            d.ChapterValidation(
                passed=False,
                issues=["第二场因果断裂"],
                failed_scene_index=1,
                node_completed=False,
                completion_quote="",
            ),
            EditorAuditResult(passed=True, issues=[]),
        ]
    )
    ok = await d.validate_chapter(Session(), provider=provider, work=work, run=run)
    assert ok is False
    prod = run.generation_context["production"]
    assert prod["phase"] == "WRITE_SCENE"
    assert prod["accepted"] == []
    assert prod["script_protocol"]["scene_index"] == 1


def test_price_book_cache_not_free_when_unpriced():
    from regent.novel.domain.price_book import (
        actual_minor,
        cache_avoided_minor,
        cache_usage_report,
        lookup,
    )

    # qwen-plus 未配置缓存价：命中不得按 0 计
    priced = actual_minor("qwen-plus", input_tokens=1_000_000, output_tokens=0, cached_input_tokens=1_000_000)
    uncached = actual_minor("qwen-plus", input_tokens=1_000_000, output_tokens=0, cached_input_tokens=0)
    assert priced >= uncached * 0.5
    assert priced > 1
    # deepseek 明确配置了缓存价：命中应低于未命中
    d_hit = actual_minor("deepseek-chat", input_tokens=1_000_000, output_tokens=0, cached_input_tokens=1_000_000)
    d_miss = actual_minor("deepseek-chat", input_tokens=1_000_000, output_tokens=0, cached_input_tokens=0)
    assert d_hit < d_miss
    assert lookup("deepseek-chat").cached_input_minor_per_mtok > 0
    avoided = cache_avoided_minor(
        "deepseek-chat", input_tokens=1_000_000, cached_input_tokens=1_000_000
    )
    assert avoided == d_miss - d_hit
    report = cache_usage_report(
        "deepseek-chat",
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=500_000,
    )
    assert report["cached_input_tokens"] == 500_000
    assert report["cache_hit_rate"] == 0.5
    assert int(report["cache_avoided_minor"]) > 0


def test_evaluate_scene_audit_shared_gate():
    from regent.novel.domain.scene_card import BeatVerdict, evaluate_scene_audit

    verdicts = [
        BeatVerdict(beat_id="b1", landed=True, evidence="根本不在正文"),
    ]
    gate = evaluate_scene_audit(
        must_beat_ids={"b1", "b2"},
        verdicts=verdicts,
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=["资源冲突"],
        scene_text="他把钥匙放在桌上。",
        working_state={"钥匙": "桌上"},
    )
    assert gate.blocking
    assert "b2" in gate.critical_missed  # 补全缺失判定
    assert any("evidence_ungrounded" in e for e in gate.evidence_fails)
    assert gate.continuity_block
    assert gate.needs_revision()
    assert "连续性" in gate.revision_instruction()
    tags = gate.hard_fail_tags("s1")
    assert any(t.startswith("s1:") for t in tags)


def test_empty_entry_discovery_continuity_is_not_blocking():
    """空入场 + 「不知道→发现」类场记句：不硬阻断（df765114 误杀复现）。"""
    from regent.novel.domain.scene_card import BeatVerdict, evaluate_scene_audit

    prose = "沈默蹲下去，从座钟底座摸出一把黄铜小钥匙，再打开暗格取出怀表。"
    gate = evaluate_scene_audit(
        must_beat_ids={"b1"},
        verdicts=[
            BeatVerdict(
                beat_id="b1",
                landed=True,
                evidence="从座钟底座摸出一把黄铜小钥匙",
            )
        ],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[
            "入场状态中沈默不知道暗格里有怀表，但正文中他直接找到暗格并取出怀表，缺少发现暗格的过程。",
            "入场状态中沈默不知道黄铜小钥匙的存在，但正文中他从座钟底座摸出钥匙，与入场状态矛盾。",
        ],
        scene_text=prose,
        working_state={},
    )
    assert not gate.continuity_block
    assert not gate.blocking
    assert gate.continuity_issues  # 仍保留记录，仅不硬杀


def test_entry_vs_ending_state_lag_is_not_blocking():
    """入场状态与上场结尾互相打架：摘要滞后，不硬拦（858ad0b6 ch2）。"""
    from regent.novel.domain.scene_card import BeatVerdict, evaluate_scene_audit

    gate = evaluate_scene_audit(
        must_beat_ids={"b1"},
        verdicts=[BeatVerdict(beat_id="b1", landed=True, evidence="他把门推开")],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[
            "入场状态称『门被铁链锁死』，但上场结尾明确写『铁链哗啦落地』『推开门』，与上场结尾直接冲突。",
            "入场状态称怀表加速倒转，但上场结尾写怀表已停转，与上场结尾矛盾。",
        ],
        scene_text="他把门推开。怀表安静地贴着掌心。",
        working_state={"门": "铁链锁死", "怀表": "倒转"},
    )
    assert not gate.continuity_block
    assert not gate.blocking


def test_nonempty_entry_continuity_still_blocks():
    from regent.novel.domain.scene_card import BeatVerdict, evaluate_scene_audit

    gate = evaluate_scene_audit(
        must_beat_ids={"b1"},
        verdicts=[BeatVerdict(beat_id="b1", landed=True, evidence="他把钥匙放在桌上")],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=["入场状态中钥匙应在抽屉，正文却写成桌上"],
        scene_text="他把钥匙放在桌上。",
        working_state={"钥匙": "抽屉"},
    )
    assert gate.continuity_block
    assert gate.blocking


def test_evidence_only_warn_does_not_block_if_not_must():
    """非 must 节拍的证据失败：needs_revision 但不 blocking。"""
    from regent.novel.domain.scene_card import BeatVerdict, evaluate_scene_audit

    gate = evaluate_scene_audit(
        must_beat_ids={"b1"},
        verdicts=[
            BeatVerdict(beat_id="b1", landed=True, evidence="他把钥匙放在桌上"),
            BeatVerdict(beat_id="b9", landed=True, evidence="根本不在正文"),
        ],
        hard_fails=[],
        continuity_ok=True,
        continuity_issues=[],
        scene_text="他把钥匙放在桌上。",
    )
    assert not gate.blocking
    assert gate.needs_revision()
    assert any("evidence_ungrounded" in e for e in gate.evidence_fails)


def test_evidence_grounded_tolerates_punctuation():
    from regent.novel.domain.scene_card import evidence_grounded

    prose = "沈星野把开场词改掉，只改一句，尺度匹配当场录制。"
    assert evidence_grounded("沈星野把开场词改掉只改一句", prose)
    assert not evidence_grounded("完全无关的一句话啊啊啊啊啊啊", prose)


def test_normalize_opening_must_show_caps_first_scene():
    from regent.novel.domain.scene_card import (
        ChapterScenePlan,
        SceneCard,
        StructuredBeat,
        normalize_opening_must_show,
    )

    plan = ChapterScenePlan(
        chapter_no=1,
        cards=[
            SceneCard(
                scene_id="s1",
                purpose="开篇",
                beats=[
                    StructuredBeat(beat_id="b1", text="站稳", must_show=True),
                    StructuredBeat(beat_id="b2", text="规则", must_show=True),
                    StructuredBeat(beat_id="b3", text="兑现", must_show=True),
                    StructuredBeat(beat_id="b4", text="代价", must_show=True),
                ],
            ),
            SceneCard(
                scene_id="s2",
                purpose="兑现",
                beats=[StructuredBeat(beat_id="b5", text="钩子", must_show=True)],
            ),
        ],
    )
    out = normalize_opening_must_show(plan, first_scene_must_cap=2)
    assert [b.must_show for b in out.cards[0].beats] == [True, True, False, False]
    assert out.cards[1].beats[0].must_show is True


def test_scene_write_system_shared_stable():
    from regent.novel.domain.scene_card import SCENE_WRITE_SYSTEM
    from regent.novel.experiments import scene_exec as sx
    from regent.novel.application import direction as d

    assert "{target}" not in SCENE_WRITE_SYSTEM
    assert "target_chars" in SCENE_WRITE_SYSTEM
    assert sx._SCENE_WRITE_SYSTEM is SCENE_WRITE_SYSTEM or sx._SCENE_WRITE_SYSTEM == SCENE_WRITE_SYSTEM
    # 生产 WRITE_SCENE 必须引用同一常量
    import inspect

    src = inspect.getsource(d._produce_script_tick)
    assert "SCENE_WRITE_SYSTEM" in src


def _sp_with_plan(*, scene_idx: int, srep: dict | None = None):
    card = _card("s1", "b1")
    card2 = _card("s2", "b2")
    sp = empty_script_state()
    sp.update(
        {
            "scene_plan": {
                "cards": [card.model_dump(mode="json"), card2.model_dump(mode="json")],
                "chapter_goal": "x",
            },
            "scene_index": scene_idx,
            "scene_texts": [TEXT] * max(0, scene_idx),
            "creative_repairs": 0,
        }
    )
    if srep:
        sp.update(srep)
    return sp


@pytest.mark.asyncio
async def test_call_broker_second_scene_and_rewrite_no_conflict(novel_db):
    """真实 CallBroker：审校回退后 takes 非空，第二场与重写不得撞幂等键。"""
    from sqlalchemy import select
    from regent.novel.application import production as prod
    from regent.novel.infrastructure.models import (
        ModelCallModel,
        NovelPrincipalModel,
        StoryWorkModel,
    )

    class Echo(BaseModel):
        text: str = ""

    class P:
        def __init__(self):
            self.n = 0

        async def generate_structured(self, *, response_model, **kwargs):
            self.n += 1
            from regent.model import ModelUsage, StructuredModelResponse

            return StructuredModelResponse(
                output=Echo(text=f"out{self.n}"),
                usage=ModelUsage(10, 10),
                model="test",
            )

    run = SimpleNamespace(input_version=1, generation_context={})
    production_stub = {"decisions": [], "call_key_version": 2}
    # 审校回退后 takes 已存在——旧路径会走 _command_id 忽略 scene_id
    takes_nonempty_marker = True
    assert takes_nonempty_marker

    sp_s1 = _sp_with_plan(scene_idx=0)
    sp_s2 = _sp_with_plan(scene_idx=1)
    sp_s1_fix = _sp_with_plan(scene_idx=0, srep={"scene_repairs:s1": 1})
    id_s1 = d._script_scene_command_id(run, production_stub, sp_s1, "WRITE_SCENE")
    id_s2 = d._script_scene_command_id(run, production_stub, sp_s2, "WRITE_SCENE")
    id_s1r = d._script_scene_command_id(run, production_stub, sp_s1_fix, "WRITE_SCENE")
    assert len({id_s1, id_s2, id_s1r}) == 3
    # 章核验回退重写：vrep 必须进键，否则同 srep 不同入参 → CallConflict（4a7e 实跑）
    sp_vrep = _sp_with_plan(scene_idx=1)
    sp_vrep["chapter_validate_repairs"] = 1
    id_vrep = d._script_scene_command_id(run, production_stub, sp_vrep, "WRITE_SCENE")
    assert id_vrep != id_s2
    assert ":vrep1:" in id_vrep
    assert ":vrep0:" in id_s2
    # F6：缺省 call_key_version → 旧格式，vrep=0 不带段
    id_legacy = d._script_scene_command_id(run, {"decisions": []}, sp_s2, "WRITE_SCENE")
    assert ":vrep" not in id_legacy

    provider = P()
    async with novel_db() as session:
        owner = uuid.uuid4()
        session.add(
            NovelPrincipalModel(id=owner, subject=f"script-scene-cmd:{owner}")
        )
        work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="测")
        session.add(work)
        await session.flush()
        run_id = uuid.uuid4()
        broker = prod.CallBroker(lease_owner="test")
        for cmd, payload in (
            (id_s1, "s1-body"),
            (id_s2, "s2-body"),
            (id_s1r, "s1-rewrite"),
        ):
            result = await broker.run(
                session,
                provider=provider,
                schema=Echo,
                work_id=work.id,
                run_id=run_id,
                chapter_no=1,
                step="PRODUCE",
                purpose="script:WRITE_SCENE",
                command_id=cmd,
                system_prompt="sys",
                user_prompt=payload,
            )
            assert result.output.text
        # 同 id 恢复应复用，不冲突
        reused = await broker.run(
            session,
            provider=provider,
            schema=Echo,
            work_id=work.id,
            run_id=run_id,
            chapter_no=1,
            step="PRODUCE",
            purpose="script:WRITE_SCENE",
            command_id=id_s1,
            system_prompt="sys",
            user_prompt="s1-body",
        )
        assert reused.reused
        rows = (await session.execute(select(ModelCallModel))).scalars().all()
        assert len(rows) == 3, [r.logical_call_id for r in rows]
        assert provider.n == 3


def test_ground_beat_evidence_shared_rule():
    from regent.novel.domain.scene_card import BeatVerdict, ground_beat_evidence

    ok = [BeatVerdict(beat_id="b1", landed=True, evidence="他把钥匙放在桌上。")]
    fails = ground_beat_evidence(ok, TEXT)
    assert fails == []
    assert ok[0].landed is True

    bad = [BeatVerdict(beat_id="b2", landed=True, evidence="根本不存在的摘录")]
    fails2 = ground_beat_evidence(bad, TEXT)
    assert fails2 and "evidence_ungrounded" in fails2[0]
    assert bad[0].landed is False

    missing = [BeatVerdict(beat_id="b3", landed=True, evidence="")]
    fails3 = ground_beat_evidence(missing, TEXT)
    assert fails3 and "evidence_missing" in fails3[0]


@pytest.mark.asyncio
async def test_real_protocol_x_grounds_ungrounded_evidence(monkeypatch):
    """实验 X：landed 但证据不在正文 → 必须阻断，不得只靠场记自证。"""
    from regent.novel.experiments import scene_exec as sx
    from regent.novel.domain.script_protocol import assemble_production_packet

    selected = ChapterScript(
        title_line="甲",
        end_change="变",
        beats=["选择", "代价", "钩子"],
        cost="代价",
        cost_type="暴露",
        power_payoff="兑现",
        turning_point="选择",
    )
    choice = ScriptChoice(selected_id="alpha", must_land_beats=["选择", "代价"])
    packet = assemble_production_packet(
        selected=selected,
        choice=choice,
        meta={"fragment_id": "f1_appraisal", "protocol": PROTOCOL_SCRIPT_SCENE},
    )
    plan = sx.ScenePlanDraft(
        cards=[_card("s1", "b1")],
        chapter_goal="变",
    )
    long_prose = "他抬眼看向镜头，掌心发烫。" * 20

    async def fake_audit(*args, **kwargs):
        return sx.SceneAudit(
            scene_id="s1",
            beat_verdicts=[
                BeatVerdict(
                    beat_id="b1",
                    landed=True,
                    evidence="这段摘录根本不在正文里",
                    note="",
                )
            ],
            continuity_ok=True,
            hard_fails=[],
            facts=[],
            state_changes={},
        )

    class XProvider(Provider):
        async def generate_structured(self, *, response_model, **kwargs):
            name = response_model.__name__
            if name == "ScenePlanDraft":
                return StructuredModelResponse(
                    output=plan.model_copy(deep=True),
                    usage=ModelUsage(1, 1),
                    model="test",
                )
            if name == "SceneProseWithBeats":
                out = sx.SceneProseWithBeats(
                    content=long_prose,
                    beat_evidence={"b1": "他抬眼看向镜头"},
                )
                return StructuredModelResponse(
                    output=out, usage=ModelUsage(1, 1), model="test"
                )
            raise AssertionError(f"unexpected schema {name}")

    monkeypatch.setattr(sx, "_audit_scene", fake_audit)
    monkeypatch.setattr(sx, "validate_scene_plan", lambda *a, **k: [])
    meter = qa.BudgetMeter(call_cap=40)
    provider = qa.MeteredProvider(XProvider([]), meter)
    frag = qa.fragment_by_id("f1_appraisal")
    result = await sx.run_protocol_x(
        provider, frag, max_revisions=0, fixed_packet=packet
    )
    assert result.completed is False
    assert "scene_hard_fail" in str(result.stop_reason)
    hard = result.hard_fail_count or 0
    fails = list(result.artifacts.get("scene_hard_fails") or [])
    assert any("evidence_ungrounded" in x for x in fails), fails
    assert hard >= 1


@pytest.mark.asyncio
async def test_produce_tick_command_id_with_takes_present(monkeypatch):
    """takes 非空（审校回退）时，produce_tick 仍必须用场景感知 command_id。"""
    captured: list[str] = []

    async def capture_call(
        session, provider, work, run, production, schema, system, payload, purpose, command_id
    ):
        captured.append(command_id)
        # 返回最小合法对象
        if schema is d._SceneProseWithBeats:
            return d._SceneProseWithBeats(
                content=TEXT,
                beat_evidence={"b1": "他把钥匙放在桌上。"},
            )
        raise AssertionError(schema)

    from regent.novel.application import directing_script_loop

    monkeypatch.setattr(directing_script_loop, "_call", capture_call)
    card = _card("s1", "b1")
    card2 = _card("s2", "b2")
    run, work = _base_run(
        phase="WRITE_SCENE",
        sp_extra={
            "scene_plan": {
                "cards": [card.model_dump(mode="json"), card2.model_dump(mode="json")],
                "chapter_goal": "x",
            },
            "scene_index": 1,  # 第二场
            "scene_texts": [TEXT],
            "working_state": {"a": "1"},
            "working_summary": "上场",
            "creative_repairs": 0,
        },
        take_content=TEXT,
    )
    # 模拟审校回退后 takes 仍在
    assert run.generation_context["production"]["takes"]
    await d.produce_tick(Session(), provider=Provider([]), work=work, run=run)
    assert captured, "应发起 WRITE_SCENE 调用"
    cid = captured[0]
    assert "sc1:s2" in cid, cid
    assert "WRITE_SCENE" in cid
    # 不得退化成 take 维度标识
    assert ":turn" not in cid
