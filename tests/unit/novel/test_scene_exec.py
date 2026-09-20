"""场景逐场演绎协议 X 与长线账本的单测。"""

from __future__ import annotations

import json

import pytest

from regent.novel.domain.scene_card import (
    BeatVerdict,
    SceneCard,
    SceneDeltas,
    SceneSupervisorReport,
    StructuredBeat,
    assemble_chapter,
    beat_index,
    compile_scene_plan,
    invalidate_dependent_scenes,
    must_land_ids,
)
from regent.novel.domain.script_protocol import ChapterScript, ScriptChoice
from regent.novel.domain.story_ledger import (
    AbilityLedger,
    AbilityResource,
    AbilityRule,
    AbilityUse,
    ArcBeat,
    EventFingerprint,
    StoryLedger,
    default_gender_bend_ledger,
)
from regent.novel.experiments import quality_ab as qa


# ---------------------------------------------------------------------------
# scene_card
# ---------------------------------------------------------------------------


def _sample_script() -> ChapterScript:
    return ChapterScript(
        title_line="测试章",
        start_state="开场压力已在门口",
        end_change="关系与把柄发生变化",
        protagonist_want="在代价最小时保住底线",
        opposition="对方逼其当场表态",
        beats=[
            "对方施加可见压力",
            "主角权衡后做出带代价的选择",
            "对方回应，局面更紧",
            "代价落地",
            "钩子升起",
        ],
        turning_point="主角权衡后做出带代价的选择",
        cost="金手指暴露给在场第三人",
        cost_type="暴露",
        power_payoff="用金手指读出对方隐瞒的关键证据并当场反制",
    )


def test_four_candidate_slots_and_inspiration_fields():
    from regent.novel.domain.script_protocol import (
        SCRIPT_CANDIDATE_SLOTS,
        SCRIPT_PHASES,
        ScriptAssignmentBoard,
        ScriptChoice,
        ScriptWriterTask,
        assemble_production_packet,
        normalize_assignment_board,
        route_script_hive,
        task_for_slot,
        ChapterScript,
    )

    assert SCRIPT_CANDIDATE_SLOTS == ("alpha", "beta", "gamma", "delta")
    assert SCRIPT_PHASES[0] == "BRIEF"
    assert "WRITE_SCRIPTS" in SCRIPT_PHASES
    assert "SCRIPT_ALPHA" not in SCRIPT_PHASES

    board = ScriptAssignmentBoard(
        goal_restated="兑现信息差",
        tasks=[
            ScriptWriterTask(slot="alpha", task="公开改写当面结果"),
            ScriptWriterTask(slot="beta", task="先稳住再反噬对手"),
            ScriptWriterTask(slot="gamma", task="借第三方施压"),
        ],
    )
    assignment = normalize_assignment_board(board)
    assert "公开" in task_for_slot(assignment, "alpha")
    assert "delta" in assignment  # 缺槽有宽松占位，不发明剧情

    payloads = {
        sid: {"candidate_slot": sid, "writer_task": task_for_slot(assignment, sid)}
        for sid in SCRIPT_CANDIDATE_SLOTS
    }
    hive = route_script_hive(payloads)
    assert hive["enabled"] is True
    assert hive["reason"] == "isolated_and_concurrent"
    leaky = {
        "alpha": {**payloads["alpha"], "candidates": {"beta": {"x": 1}}},
        "beta": payloads["beta"],
    }
    assert route_script_hive(leaky)["enabled"] is False

    choice = ScriptChoice(
        selected_id="gamma",
        reason="借刀更可信",
        inspiration_from=["delta"],
        borrowable_ideas=["丁的小试探节奏"],
        must_land_beats=["兑现"],
    )
    script = ChapterScript(
        title_line="测",
        end_change="改写结果",
        cost="烧掉缓存",
        cost_type="缓存",
        power_payoff="信息差",
        turning_point="当场改写",
    )
    packet = assemble_production_packet(selected=script, choice=choice)
    assert packet["direction"]["selected_id"] == "gamma"
    assert "小试探" in packet["direction"]["allow_writer_room"]


def test_compile_scene_plan_assigns_beat_ids():
    plan = compile_scene_plan(_sample_script(), ScriptChoice(selected_id="alpha"), chapter_no=1)
    assert 2 <= len(plan.cards) <= 4
    all_beats = beat_index(plan)
    # 骨架会补全脚本中的转折/代价/结尾，节拍数不少于原始 beats
    assert len(all_beats) >= 5
    for bid, beat in all_beats.items():
        assert bid.startswith("b")
        assert beat.text
        assert beat.scene_slot < len(plan.cards)


def test_validate_scene_plan_requires_real_coverage():
    from regent.novel.domain.scene_card import validate_scene_plan

    script = _sample_script()
    choice = ScriptChoice(
        selected_id="alpha",
        must_land_beats=["做出带代价的选择", "金手指暴露"],
    )
    plan = compile_scene_plan(script, choice, chapter_no=1)
    issues = validate_scene_plan(plan, script, choice, max_scenes=4)
    assert issues == [], issues

    # 必须映射到真实节拍：全未命中 → 问题列表非空
    bad = ScriptChoice(selected_id="alpha", must_land_beats=["完全不存在的虚构节拍XYZ"])
    issues2 = validate_scene_plan(plan, script, bad, max_scenes=4)
    assert any(i.startswith("must_land_beats_unmatched") for i in issues2), issues2

    # 转折未覆盖：去掉含转折的节拍后应报覆盖问题
    from copy import deepcopy

    stripped = deepcopy(plan)
    for card in stripped.cards:
        card.beats = [b for b in card.beats if "权衡" not in b.text and "选择" not in b.text]
    stripped.cards = [c for c in stripped.cards if c.beats] or stripped.cards
    issues3 = validate_scene_plan(
        stripped, script, ScriptChoice(selected_id="alpha"), max_scenes=4
    )
    assert any("turning_point_not_covered" in i for i in issues3), issues3


def test_compile_scene_plan_must_land_matching():
    plan = compile_scene_plan(
        _sample_script(),
        ScriptChoice(selected_id="alpha", must_land_beats=["做出带代价的选择", "代价落地"]),
        chapter_no=1,
    )
    ids = must_land_ids(plan, ["做出带代价的选择", "代价落地"])
    assert len(ids) == 2
    assert all(i.startswith("b") for i in ids)


def test_invalidate_dependent_scenes():
    plan = compile_scene_plan(_sample_script(), ScriptChoice(selected_id="alpha"), chapter_no=1)
    first = plan.cards[0].scene_id
    invalidated = invalidate_dependent_scenes(plan, first)
    assert first in invalidated
    assert len(invalidated) == len(plan.cards)


def test_assemble_chapter_merges_texts():
    cards = [
        SceneCard(scene_id="s1", purpose="a", beats=[]),
        SceneCard(scene_id="s2", purpose="b", beats=[]),
    ]
    assembled = assemble_chapter([(cards[0], "第一场正文。"), (cards[1], "第二场正文。")])
    assert "第一场正文。" in assembled.content
    assert "第二场正文。" in assembled.content
    assert len(assembled.scene_boundaries) == 2


# ---------------------------------------------------------------------------
# story_ledger
# ---------------------------------------------------------------------------


def test_event_ledger_detects_similar():
    led = StoryLedger()
    led.events.add(
        EventFingerprint(chapter_no=20, kind="公开打脸", actors=("沈星野", "顾宴川"), summary="心动问答提前剪辑录音威胁")
    )
    similar = led.events.find_similar(
        kind="公开打脸", actors=("沈星野",), summary="心动问答提前剪辑录音威胁"
    )
    assert len(similar) == 1
    different = led.events.find_similar(kind="匿名举报", actors=("沈星野",), summary="母亲手机号")
    assert len(different) == 0


def test_ability_ledger_exhausted_blocks_reuse():
    led = AbilityLedger(
        rule=AbilityRule(
            name="文抄公",
            mechanism="预知",
            resources=[
                AbilityResource(
                    resource_id="future_cache_once",
                    label="最后一次缓存",
                    aliases=("最后缓存",),
                )
            ],
        )
    )
    led.record(
        AbilityUse(
            chapter_no=5,
            what="抄名场面",
            resource_id="future_cache_once",
            resource_spent="最后一次缓存",
            irreversible=True,
        )
    )
    assert "future_cache_once" in led.exhausted
    with pytest.raises(ValueError, match="已消耗"):
        led.assert_not_exhausted("最后一次缓存")
    with pytest.raises(ValueError, match="已消耗"):
        led.assert_not_exhausted("future_cache_once")
    # 提及/否定不视为再次动用
    assert led.fact_reuses_exhausted("没有再次使用最后一次缓存") == ""
    # 再次动用才拒
    assert led.fact_reuses_exhausted("再次动用最后一次缓存翻盘") == "future_cache_once"


def test_classify_ability_fact_actions():
    from regent.novel.domain.story_ledger import classify_ability_fact

    led = default_gender_bend_ledger()
    assert led.ability is not None
    action, rid = classify_ability_fact("透支未来情报缓存换镜头", led.ability)
    assert action in {"use", "deplete"}
    assert rid == "future_cache"
    action2, _ = classify_ability_fact("没有再次使用未来情报缓存", led.ability)
    assert action2 == "mention"

def test_hook_open_close():
    led = StoryLedger()
    led.open_hook("制作组盯上沈星野")
    assert len(led.open_hooks) == 1
    led.close_hook("制作组盯上沈星野")
    assert led.open_hooks == []
    assert len(led.closed_hooks) == 1


def test_ledger_serialization_roundtrip():
    led = default_gender_bend_ledger()
    led.events.add(EventFingerprint(chapter_no=1, kind="公开打脸", summary="拆穿剧本宠粉"))
    led.record_arc("沈星野", ArcBeat(chapter_no=1, costly_choice="公开反杀"))
    data = led.to_dict()
    restored = StoryLedger.from_dict(data)
    assert len(restored.events.events) == 1
    assert "沈星野" in restored.arcs
    assert restored.ability is not None
    assert restored.ability.rule.name == "文抄公"


def test_prompt_block_includes_exhausted_and_events():
    led = default_gender_bend_ledger()
    led.events.add(EventFingerprint(chapter_no=3, kind="录音威胁", summary="剪辑录音威胁诊断书"))
    led.ability.record(
        AbilityUse(
            chapter_no=5,
            what="烧掉缓存",
            resource_id="future_cache",
            resource_spent="未来情报缓存",
        )
    )
    block = led.prompt_block(chapter_no=6, focus_characters=("沈星野",))
    assert "文抄公" in block
    assert "已耗尽" in block or "录音威胁" in block
    assert "故事段" in block
    assert "future_cache" in led.ability.exhausted


# ---------------------------------------------------------------------------
# protocol X
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sim_protocol_x_completes():
    meter = qa.BudgetMeter(call_cap=40)
    provider = qa.MeteredProvider(qa.SimProvider(), meter)  # type: ignore[arg-type]
    frag = qa.fragment_by_id("f1_appraisal")
    from regent.novel.experiments.scene_exec import run_protocol_x

    result = await run_protocol_x(provider, frag, max_revisions=1)
    assert result.completed
    assert result.protocol == "X"
    assert result.facts_committed
    assert result.artifacts["facts_status"] == "committed_from_final_prose"
    assert "compile_scenes_try1" in result.steps_log or "compile_scenes" in result.steps_log
    assert "extract_facts_from_prose" in result.steps_log
    assert result.artifacts.get("scene_plan")
    assert result.artifacts.get("scene_audits")


@pytest.mark.asyncio
async def test_protocol_x_registered():
    result = await qa.run_sample(
        protocol="X",
        fragment_id="f2_reborn",
        provider=qa.SimProvider(),  # type: ignore[arg-type]
    )
    assert result.completed
    assert result.protocol == "X"
    assert result.artifacts.get("hooks_opened") is not None


@pytest.mark.asyncio
async def test_protocol_x_reject_both_stops():
    """真实触发 reject_both：ScriptChoice 返回 reject_both 时协议应停止且不产出正文。"""
    from regent.model.provider import StructuredModelResponse, ModelUsage
    from regent.novel.domain.script_protocol import ScriptChoice

    class RejectBothProvider(qa.SimProvider):
        async def generate_structured(self, *, system_prompt, user_prompt, response_model, temperature=0):
            if response_model.__name__ == "ScriptChoice":
                usage = ModelUsage(input_tokens=40, output_tokens=80, request_id="sim")
                output = ScriptChoice(
                    selected_id="reject_both",
                    reason="两稿同套路",
                    weakness="代价类型相同",
                )
                return StructuredModelResponse(output=output, usage=usage, model="sim")
            return await super().generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
            )

    meter = qa.BudgetMeter(call_cap=40)
    provider = qa.MeteredProvider(RejectBothProvider(), meter)  # type: ignore[arg-type]
    frag = qa.fragment_by_id("f1_appraisal")
    from regent.novel.experiments.scene_exec import run_protocol_x

    result = await run_protocol_x(provider, frag, max_revisions=1)
    assert not result.completed
    assert result.stop_reason == "reject_both_scripts"
    assert result.hard_fail_count >= 1
    assert not result.prose
    assert result.artifacts["fault_taxonomy"]["supply"] is True


@pytest.mark.asyncio
async def test_protocol_x_fixed_packet_skips_script():
    """固定制作包：跳过编剧与选本，直接进入分场。"""
    from regent.novel.domain.script_protocol import (
        ChapterScript,
        ScriptChoice,
        assemble_production_packet,
    )

    script = ChapterScript(
        title_line="固定剧本",
        start_state="开场",
        end_change="关系变化",
        protagonist_want="保底线",
        opposition="逼表态",
        beats=["对方施压", "主角权衡", "做出选择", "代价落地"],
        turning_point="做出选择",
        cost="暴露",
        cost_type="暴露",
        power_payoff="用金手指反制",
    )
    choice = ScriptChoice(selected_id="alpha", must_land_beats=["做出选择", "代价落地"])
    packet = assemble_production_packet(
        selected=script, choice=choice, meta={"fragment_id": "f1_appraisal"}
    )

    meter = qa.BudgetMeter(call_cap=40)
    provider = qa.MeteredProvider(qa.SimProvider(), meter)  # type: ignore[arg-type]
    frag = qa.fragment_by_id("f1_appraisal")
    from regent.novel.experiments.scene_exec import run_protocol_x

    result = await run_protocol_x(provider, frag, max_revisions=1, fixed_packet=packet)
    assert result.artifacts.get("fixed_packet") is True
    assert "use_fixed_packet" in result.steps_log
    assert "write_script_alpha" not in result.steps_log
    assert "director_select" not in result.steps_log


def test_script_scene_architecture_registered_in_production():
    """director_script_scene 已接入生产 executor 注册表。"""
    from regent.novel.application import executor as executor_app
    from regent.novel.application.direction import (
        PROTOCOL_SCRIPT_SCENE,
        SCRIPT_SCENE_ARCHITECTURE,
        production_protocol,
    )

    assert SCRIPT_SCENE_ARCHITECTURE in executor_app.KNOWN_EXECUTORS
    assert executor_app.executor_version(SCRIPT_SCENE_ARCHITECTURE) == "director_script_scene@1"

    class _FakeRun:
        generation_context = {"architecture_version": SCRIPT_SCENE_ARCHITECTURE}

    assert production_protocol(_FakeRun()) == PROTOCOL_SCRIPT_SCENE

    # production dict 显式钉 protocol 时优先
    assert production_protocol({"protocol": PROTOCOL_SCRIPT_SCENE}) == PROTOCOL_SCRIPT_SCENE


def test_script_scene_phases_include_scene_loop():
    from regent.novel.domain.script_protocol import SCRIPT_SCENE_PHASES

    phases = list(SCRIPT_SCENE_PHASES)
    assert "COMPILE_SCENES" in phases
    assert "WRITE_SCENE" in phases
    assert "AUDIT_SCENE" in phases
    assert "ASSEMBLE_CHAPTER" in phases
    # 相位顺序：分场在选本之后，组装在场记之后
    assert phases.index("COMPILE_SCENES") > phases.index("ASSEMBLE")
    assert phases.index("ASSEMBLE_CHAPTER") > phases.index("AUDIT_SCENE")
    assert phases.index("VALIDATE") > phases.index("ASSEMBLE_CHAPTER")
    # 分场臂不得把整章 WRITE_CHAPTER 当合法阶段（否则会假返工后撞白名单）
    assert "WRITE_CHAPTER" not in phases
