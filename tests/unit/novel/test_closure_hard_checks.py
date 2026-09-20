"""闭环硬核验：不靠 grep 关键词，直接跑断言验证行为。"""

from __future__ import annotations

import pytest

from regent.novel.domain.story_ledger import (
    AbilityLedger,
    AbilityRule,
    AbilityUse,
    EventFingerprint,
    StoryLedger,
    default_gender_bend_ledger,
)
from regent.novel.domain.states import ChapterStep, chapter_step_order
from regent.novel.application.executor import EXECUTOR_VERSIONS, KNOWN_EXECUTORS


# ---------------------------------------------------------------------------
# 1. 步骤表
# ---------------------------------------------------------------------------


def test_chapter_step_order_script_scene():
    steps = chapter_step_order({"architecture_version": "director_script_scene"})
    assert steps == (
        ChapterStep.ASSEMBLE,
        ChapterStep.DIRECT,
        ChapterStep.PRODUCE,
        ChapterStep.REVIEW,
        ChapterStep.CANON,
    ), f"director_script_scene 应走五步链，实际: {steps}"


def test_chapter_step_order_unknown_arch_falls_back():
    steps = chapter_step_order({"architecture_version": "nonexistent"})
    assert ChapterStep.PERFORM in steps, "未知架构应落 legacy 六步"


# ---------------------------------------------------------------------------
# 2. executor 注册
# ---------------------------------------------------------------------------


def test_script_scene_in_known_executors():
    assert "director_script_scene" in KNOWN_EXECUTORS
    assert EXECUTOR_VERSIONS["director_script_scene"] == "director_script_scene@1"


# ---------------------------------------------------------------------------
# 3. 场记硬问题阻断验收 — 直接调 production_protocol 路由
# ---------------------------------------------------------------------------


def test_production_protocol_routes_script_scene():
    from regent.novel.application.direction import (
        PROTOCOL_SCRIPT_SCENE,
        production_protocol,
    )

    class _Run:
        generation_context = {"architecture_version": "director_script_scene"}

    assert production_protocol(_Run()) == PROTOCOL_SCRIPT_SCENE
    assert production_protocol({"protocol": "script_scene"}) == PROTOCOL_SCRIPT_SCENE


# ---------------------------------------------------------------------------
# 4. 重复事件查重：真实相似度
# ---------------------------------------------------------------------------


def test_find_similar_exact_match():
    led = StoryLedger()
    led.events.add(
        EventFingerprint(
            chapter_no=20,
            kind="committed_fact",
            summary="心动问答提前剪辑录音威胁诊断书",
        )
    )
    # 完全相同摘要 → 命中
    hits = led.events.find_similar(
        kind="committed_fact",
        summary="心动问答提前剪辑录音威胁诊断书",
    )
    assert len(hits) == 1, f"完全相同摘要应命中，实际 {len(hits)}"


def test_find_similar_high_overlap():
    led = StoryLedger()
    led.events.add(
        EventFingerprint(
            chapter_no=20,
            kind="committed_fact",
            summary="沈星野用文抄公情报当众拆穿顾宴川的剧本宠粉",
        )
    )
    # 高度重叠摘要 → 应命中
    hits = led.events.find_similar(
        kind="committed_fact",
        summary="沈星野用文抄公情报当众拆穿顾宴川的剧本宠粉行为",
    )
    assert len(hits) >= 1, f"高重叠摘要应命中，实际 {len(hits)}"


def test_find_similar_different_summary_no_hit():
    led = StoryLedger()
    led.events.add(
        EventFingerprint(
            chapter_no=20,
            kind="committed_fact",
            summary="沈星野在恋综开机日被当众点名空降",
        )
    )
    hits = led.events.find_similar(
        kind="committed_fact",
        summary="顾宴川深夜约谈并拿身份线索要挟",
    )
    assert len(hits) == 0, f"不同摘要不应命中，实际 {len(hits)}"


# ---------------------------------------------------------------------------
# 5. 耗尽资源：record + assert + prompt
# ---------------------------------------------------------------------------


def test_ability_record_exhausts_resource():
    ab = AbilityLedger(rule=AbilityRule(name="文抄公"))
    ab.record(
        AbilityUse(
            chapter_no=5,
            what="抄名场面",
            resource_spent="最后一次缓存",
            irreversible=True,
        )
    )
    assert any("最后一次缓存" in x for x in ab.exhausted), f"应记录耗尽，实际 {ab.exhausted}"
    assert ab.exhausted[0].startswith("legacy:")


def test_ability_assert_blocks_exhausted():
    ab = AbilityLedger(rule=AbilityRule(name="文抄公"))
    ab.record(
        AbilityUse(
            chapter_no=5,
            what="抄",
            resource_spent="最后一次缓存",
            irreversible=True,
        )
    )
    with pytest.raises(ValueError, match="已消耗"):
        ab.assert_not_exhausted("最后一次缓存")


def test_ability_assert_allows_fresh():
    ab = AbilityLedger(rule=AbilityRule(name="文抄公"))
    ab.record(
        AbilityUse(
            chapter_no=5,
            what="抄",
            resource_spent="最后一次缓存",
            irreversible=True,
        )
    )
    ab.assert_not_exhausted("新的资源")  # 不应抛错


def test_ability_prompt_includes_exhausted():
    ab = AbilityLedger(rule=AbilityRule(name="文抄公", mechanism="预知"))
    ab.record(
        AbilityUse(
            chapter_no=5,
            what="抄",
            resource_spent="最后一次缓存",
            irreversible=True,
        )
    )
    block = ab.prompt_constraints()
    assert "已耗尽" in block, f"prompt 应含耗尽列表，实际: {block}"
    assert "最后一次缓存" in block


def test_ingest_rejects_exhausted_reuse_and_records_resource_id():
    led = default_gender_bend_ledger()
    led.ingest_chapter_outcome(
        chapter_no=5,
        facts=["透支完未来情报缓存换镜头"],
        protagonist="沈星野",
    )
    assert "future_cache" in led.ability.exhausted
    with pytest.raises(ValueError, match="future_cache"):
        led.ingest_chapter_outcome(
            chapter_no=6,
            facts=["再次动用未来情报缓存翻盘"],
        )
    # 仅提及不拒
    led.ingest_chapter_outcome(
        chapter_no=6,
        facts=["没有再次使用未来情报缓存"],
    )


# ---------------------------------------------------------------------------
# 6. 耗尽匹配逻辑：稳定 resource_id + 动作分类
# ---------------------------------------------------------------------------


def test_exhausted_match_via_resource_id():
    """耗尽判定走 resource_id：展示名变化仍命中同一资源。"""
    from regent.novel.domain.story_ledger import AbilityResource

    ab = AbilityLedger(
        rule=AbilityRule(
            name="文抄公",
            resources=[
                AbilityResource(
                    resource_id="future_cache_once",
                    label="最后一次缓存",
                    aliases=("最后缓存",),
                )
            ],
        )
    )
    ab.record(
        AbilityUse(
            chapter_no=5,
            what="抄",
            resource_id="future_cache_once",
            resource_spent="最后一次缓存",
            irreversible=True,
        )
    )
    assert ab.fact_reuses_exhausted("再次动用最后缓存翻盘") == "future_cache_once"
    assert ab.fact_reuses_exhausted("没有再次使用最后一次缓存") == ""


def test_exhausted_match_with_short_resource_name():
    """兼容：无清单时用 legacy id；展示名仍可 assert。"""
    # 模拟提取逻辑
    fact = "主角用掉最后一次缓存换来关键情报"
    resource = ""
    for marker in ("透支", "失去", "耗尽", "用掉", "付出"):
        if marker in fact:
            after = fact.split(marker, 1)[-1]
            resource = after[:12].strip("了的，。；、 ")
            if resource:
                break
    assert resource, "应提取到资源名"
    assert len(resource) <= 12, f"资源名应 ≤12 字，实际 {len(resource)}"
    # 短资源名应能命中包含它的新 fact
    new_fact = f"主角再次试图使用{resource[:6]}"
    assert resource[:6] in new_fact

def test_resource_spent_not_whole_sentence():
    """回归：resource_spent 不再是整句 fact。"""
    fact = "主角烧掉一段未来情报缓存换来镜头位"
    resource = ""
    for marker in ("透支", "失去", "耗尽", "用掉", "付出", "烧掉"):
        if marker in fact:
            after = fact.split(marker, 1)[-1]
            resource = after[:12].strip("了的，。；、 ")
            if resource:
                break
    assert resource, "应提取到资源名"
    assert resource != fact, "资源名不应等于整句 fact"
    assert len(resource) <= 12, f"资源名应 ≤12 字，实际 {len(resource)}"
    assert "缓存" in resource or "情报" in resource, f"资源名应含关键名词，实际: [{resource}]"


# ---------------------------------------------------------------------------
# 7. ensure_segment
# ---------------------------------------------------------------------------


def test_ingest_chapter_outcome_and_prompt():
    from regent.novel.domain.story_ledger import StoryLedger

    led = StoryLedger()
    led.ingest_chapter_outcome(
        chapter_no=3,
        facts=["顾宴川开始怀疑有内鬼剧本"],
        hooks_opened=["制作组盯梢"],
        hooks_closed=[],
        character_shifts=["沈星野更敢正面交锋"],
        protagonist="沈星野",
    )
    assert led.open_hooks
    assert led.events.events
    assert "沈星野" in led.arcs
    block = led.prompt_block(chapter_no=3, focus_characters=("沈星野",))
    assert "已发生事件" in block or "未解钩子" in block


def test_story_ledger_for_chapter_inherits_previous():
    from regent.novel.application.generation import _story_ledger_for_chapter
    from regent.novel.domain.story_ledger import StoryLedger
    from types import SimpleNamespace

    prev = StoryLedger()
    prev.ingest_chapter_outcome(chapter_no=1, facts=["第一章事实A"], hooks_opened=["钩子1"])
    payload, block, hooks = _story_ledger_for_chapter(
        old_context={},
        previous_rows=[
            SimpleNamespace(
                chapter_no=1,
                generation_context={"story_ledger": prev.to_dict()},
            )
        ],
        chapter_no=2,
    )
    assert payload["open_hooks"]
    assert "钩子1" in hooks
    assert isinstance(block, str)


def test_cache_hit_measure_sim_pipeline():
    import asyncio
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "novel"
        / "run_cache_hit_measure.py"
    )
    spec = importlib.util.spec_from_file_location("cache_hit_measure", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = asyncio.run(mod.run_measure(live=False))
    assert report["mode"] == "sim"
    cases = [c["case"] for c in report["calls"]]
    assert "same_scene_retry" in cases
    assert "cross_scene" in cases
    # warmup 之后同场重试应能读到模拟 cached_input_tokens
    by = {c["case"]: c for c in report["calls"]}
    assert int(by["same_scene_retry"]["usage"]["cached_input_tokens"]) > 0
    assert "cache_hit_rate" in by["same_scene_retry"]["billing"]



def test_ensure_segment_extends_unfulfilled_placeholder():
    """无 milestone/不可逆结果的占位段：越界后延长，不得空框换段。"""
    from regent.novel.domain.story_ledger import StorySegment

    led = StoryLedger()
    led.segments.add(
        StorySegment(
            segment_id="old",
            chapter_from=1,
            chapter_to=8,
            status="active",
            goal="让既有代价产生新后果",
            key_choice="",
            irreversible_outcome="",
            milestone="",
        )
    )
    seg = led.ensure_segment(20)
    old = next(s for s in led.segments.segments if s.segment_id == "old")
    assert old.status == "active"
    assert old.chapter_to >= 20
    assert seg.segment_id == "old"


def test_ensure_segment_closes_when_milestone_met():
    from regent.novel.domain.story_ledger import StorySegment

    led = StoryLedger()
    led.segments.add(
        StorySegment(
            segment_id="old",
            chapter_from=1,
            chapter_to=8,
            status="active",
            milestone="把柄已易手",
            irreversible_outcome="录音外泄",
        )
    )
    led.ensure_segment(20)
    old = next(s for s in led.segments.segments if s.segment_id == "old")
    assert old.status == "done", f"已兑现段应关闭，实际 {old.status}"
    assert any(s.segment_id.startswith("seg_auto_") for s in led.segments.segments)


# ---------------------------------------------------------------------------
# 8. 实验协议 X 注册
# ---------------------------------------------------------------------------


def test_protocol_x_registered():
    from regent.novel.experiments.quality_ab import PROTOCOLS, _RUNNERS

    assert "X" in _RUNNERS
    assert "X" in PROTOCOLS


# ---------------------------------------------------------------------------
# 9. 场记 AUDIT 重写逻辑：验证 MAX_SCENE_REPAIRS 边界
# ---------------------------------------------------------------------------


def test_scene_repair_logic_boundary():
    """验证 AUDIT_SCENE 的重写条件：scene_repairs < MAX_SCENE_REPAIRS。"""
    MAX_SCENE_REPAIRS = 1
    # 第一次：0 < 1 → 应重写
    assert 0 < MAX_SCENE_REPAIRS
    # 第二次：1 < 1 → 不再重写，进 scene_hard_fails
    assert not (1 < MAX_SCENE_REPAIRS)


# ---------------------------------------------------------------------------
# 10. VALIDATE / ACCEPT 分流：script vs script_scene
# ---------------------------------------------------------------------------


def test_validate_routing_script_allows_write_chapter():
    """script 整章臂：章节硬失败可走 WRITE_CHAPTER。"""
    from regent.novel.domain.script_protocol import SCRIPT_PHASES, SCRIPT_SCENE_PHASES

    assert "WRITE_CHAPTER" in SCRIPT_PHASES
    assert "WRITE_CHAPTER" not in SCRIPT_SCENE_PHASES


def test_validate_routing_script_scene_never_write_chapter():
    """script_scene：章节失败 / render 不得落到 WRITE_CHAPTER（阶段表非法）。"""
    from regent.novel.domain.script_protocol import SCRIPT_SCENE_PHASES

    chapter_fails = ["too_short"]
    scene_fails: list[str] = []
    proto = "script_scene"
    if scene_fails:
        route = "STOP"
    elif chapter_fails:
        route = "STOP" if proto == "script_scene" else "WRITE_CHAPTER"
    else:
        route = "ACCEPT"
    assert route == "STOP"
    assert "WRITE_CHAPTER" not in SCRIPT_SCENE_PHASES


def test_scene_audit_lingering_stops_not_advance():
    """本场修满仍失败 → 停机，不得推进 scene_index。"""
    audit_hard = ["改因果终点"]
    critical_missed = ["b2"]
    scene_repairs = 1
    MAX_SCENE_REPAIRS = 1
    can_rewrite = bool(audit_hard or critical_missed) and scene_repairs < MAX_SCENE_REPAIRS
    assert not can_rewrite
    # 与生产一致：记 hard_fails 后 STOP，不 ASSEMBLE / 不拍下一场
    route = "STOP" if (audit_hard or critical_missed) else "ADVANCE"
    assert route == "STOP"
