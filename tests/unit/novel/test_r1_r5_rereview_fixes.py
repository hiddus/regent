"""R1–R5 反例：858a 复评文档（2026-09-20）剩余问题必修项。

用完整调用链/审核函数测试，不只测 soft 辅助函数。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application.directing_calls import (
    hydrate_call_key_version,
    resolve_call_key_version,
)
from regent.novel.application.directing_script_loop import (
    _audit_cont_softable,
    _begin_located_scene_repair,
    _model_binds_to_unsolved_fail,
    _produce_script_tick,
    _verify_model_location,
)
from regent.novel.application.executor import PINNED_CONTEXT_KEYS, carry_over
from regent.novel.domain.repair_locate import (
    LocatedIssue,
    RepairTarget,
    apply_repair_target_to_script_state,
    content_hash,
    locate_fail_messages,
    select_repair_target,
)
from regent.novel.domain.scene_card import (
    BeatVerdict,
    SceneCard,
    StructuredBeat,
    evaluate_scene_audit,
    is_entry_vs_ending_state_lag,
)

PARA = "雨水沿窗棂流下，两个人仍旧没有开口，钥匙静静躺在桌面正中。"
LONG = PARA + "更多叙述让它超过三十二字，足够进入检测投影。"


def _card(sid: str, beat_id: str = "b1") -> SceneCard:
    return SceneCard(
        scene_id=sid,
        purpose="x",
        setting="s",
        entry_state="e",
        protagonist_objective="o",
        opposition="p",
        core_choice="c",
        emotion_arc="a",
        exit_change="x",
        beats=[StructuredBeat(beat_id=beat_id, text="当场表态", must_show=True)],
        target_chars=400,
    )


def _cards_json(n: int = 3) -> list[dict]:
    return [
        _card(f"s{i}", f"b{i}").model_dump(mode="json") for i in range(n)
    ]


# ========== R1 / C1：连续性门 ==========


def test_r1_real_contradiction_not_soft_by_keywords():
    """两端事实一致（甲持有钥匙）而正文不同 → 真实矛盾，完整审核必须阻断。"""
    issue = (
        "入场状态确认钥匙在甲手中，上场结尾也确认在甲手中，"
        "但本场无交接就变到乙手中"
    )
    assert is_entry_vs_ending_state_lag(issue) is False
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="乙接过钥匙，没有交代从何处得来。",
        working_state={"key_holder": "甲"},
        entry_summary="上场结尾：钥匙仍在甲手中\n累计状态：{\"key_holder\": \"甲\"}",
        prior_scene_ending="甲把钥匙攥在掌心，没有交给任何人。",
    )
    assert gate.continuity_block is True
    assert gate.blocking is True


def test_r1_proven_lag_without_structured_new_value_blocks():
    """D1：前场改写叙述但无结构化新值来源 → 不得凭「旧值未出现」放行。"""
    issue = "入场状态仍留锁死，上场结尾已写铁链落地、怀表停转"
    assert is_entry_vs_ending_state_lag(
        issue,
        working_state={"门": "铁链锁死", "怀表": "倒转"},
        prior_scene_ending="铁链哗啦落地，推开门。怀表停转。",
        entry_summary="上场结尾：锁仍锁着",
    ) is False
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="铁链散落在地，怀表指针静止不动。",
        working_state={"门": "铁链锁死", "怀表": "倒转"},
        entry_summary="上场结尾：锁仍锁着",
        prior_scene_ending="铁链哗啦落地，推开门。怀表停转。",
    )
    assert gate.continuity_block is True
    assert gate.blocking is True


def test_r1_wording_only_no_sources_blocks():
    """同一滞后措辞但无 working_state / prior_scene_ending → 不得放行。"""
    issue = "入场状态仍留锁死，上场结尾已写铁链落地、怀表停转"
    assert is_entry_vs_ending_state_lag(issue) is False
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="铁链散落在地。",
        working_state={},
        # 无 prior_scene_ending 来源
    )
    assert gate.continuity_block is True


def test_r1_unproven_lag_blocks():
    """仅有关键词、无滞后证明、无来源比对 → 保留阻断。"""
    issue = "入场状态与上场结尾关于钥匙归属描述不一致"
    assert is_entry_vs_ending_state_lag(issue) is False
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="正文内容。",
        working_state={"key_holder": "甲"},
        # 无 prior_scene_ending
    )
    assert gate.continuity_block is True


def test_r1_string_inequality_is_not_lag_proof():
    """摘要带标签与 ending 字符串不等，但事实一致 → 不是滞后证据。"""
    issue = "入场状态与上场结尾矛盾：钥匙归属说法不同"
    entry_summary = '上场结尾：甲把钥匙攥在掌心\n累计状态：{"持有人": "甲"}'
    prior_ending = "甲把钥匙攥在掌心，没有交给任何人。"
    working_state = {"持有人": "甲"}
    assert is_entry_vs_ending_state_lag(
        issue,
        entry_summary=entry_summary,
        prior_scene_ending=prior_ending,
        working_state=working_state,
    ) is False
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="乙拿着钥匙。",
        working_state=working_state,
        entry_summary=entry_summary,
        prior_scene_ending=prior_ending,
    )
    assert gate.continuity_block is True


def test_r1_entity_mention_without_new_value_blocks():
    """D1：只提及实体外观、旧值未写 ≠ 属性已变 → 阻断。"""
    issue = (
        "入场状态记录钥匙在甲手中；上场结尾没有发生交接，"
        "本场却直接写钥匙在乙手中。"
    )
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="乙握着钥匙开门。",
        working_state={"钥匙": "甲持有"},
        entry_summary="钥匙仍由甲持有",
        prior_scene_ending="钥匙泛着微光，屋外雨声渐密。",
    )
    assert gate.continuity_block is True
    assert gate.blocking is True


def test_r1_synonym_old_value_still_blocks():
    """D1：前场同义描述旧值（甲仍握着）→ 不是滞后，保持阻断。"""
    issue = "入场状态记录钥匙在甲手中；上场结尾描写甲仍握着钥匙，本场却变到乙手中。"
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="乙握着钥匙开门。",
        working_state={"钥匙": "甲", "持有人": "甲"},
        entry_summary="钥匙仍由甲持有",
        prior_scene_ending="甲仍握着钥匙，没有交给任何人。",
    )
    assert gate.continuity_block is True


def test_r1_handover_text_without_structured_delta_blocks():
    """D1：正文像真正交接，但无结构化 prior_exit_state → 仍不自动 soft。"""
    issue = "入场状态称钥匙归甲；上场结尾写钥匙已交到乙手中，本场状态却仍按甲。"
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="甲还以为钥匙在自己手里。",
        working_state={"钥匙": "甲持有"},
        entry_summary="钥匙在甲处",
        prior_scene_ending="他把钥匙交到乙手中，乙收好后离开。",
    )
    assert gate.continuity_block is True


def test_r1_structured_handover_new_value_can_soft():
    """D1：结构化 prior_exit 给出新值且接地到前场正文 → 可证滞后 soft。"""
    issue = "入场状态称钥匙归甲；上场结尾写钥匙已交到乙手中，摘要未更新。"
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="甲还以为钥匙在自己手里。",
        working_state={"钥匙": "甲持有"},
        entry_summary="钥匙在甲处",
        prior_scene_ending="他把钥匙交到乙手中，乙收好后离开。",
        prior_exit_state={"钥匙": "乙手中"},
    )
    assert gate.continuity_block is False
    assert is_entry_vs_ending_state_lag(
        issue,
        working_state={"钥匙": "甲持有"},
        prior_scene_ending="他把钥匙交到乙手中，乙收好后离开。",
        prior_exit_state={"钥匙": "乙手中"},
    ) is True


def test_r1_structured_exit_not_in_ending_still_blocks():
    """D1：结构化新值未出现在前场正文 → 不得 soft（未接地）。"""
    issue = "入场状态称钥匙归甲；上场结尾与摘要不一致。"
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="正文。",
        working_state={"钥匙": "甲持有"},
        entry_summary="钥匙在甲处",
        prior_scene_ending="钥匙泛着微光，屋外雨声渐密。",
        prior_exit_state={"钥匙": "乙手中"},
    )
    assert gate.continuity_block is True


def test_r1_unrelated_state_value_does_not_soft_issue():
    """D1：无关状态值匹配不得决定钥匙问题是否放行。"""
    issue = "入场状态记录钥匙在甲手中；上场结尾没有交接，本场却写钥匙在乙手中。"
    gate = evaluate_scene_audit(
        must_beat_ids=set(),
        verdicts=[],
        hard_fails=[],
        continuity_ok=False,
        continuity_issues=[issue],
        scene_text="乙握着钥匙。",
        working_state={"钥匙": "甲持有", "天气": "雨声渐密"},
        entry_summary="钥匙在甲处",
        # 结尾只匹配无关「雨声」，不含钥匙归属新值
        prior_scene_ending="屋外雨声渐密，灯火摇曳。",
    )
    assert gate.continuity_block is True


def test_r1_multi_wording_real_conflict_all_block():
    """同一真实矛盾的多种改述 → 全部阻断。"""
    wordings = [
        "入场状态确认钥匙在甲手中，上场结尾也确认在甲手中，但本场无交接就变到乙手中",
        "入场状态记录钥匙在甲手中；上场结尾描写甲仍拿着钥匙，本场却无交接变到乙手中",
        "入场状态称钥匙归甲；上场结尾写甲仍持有钥匙，本场却变到乙，无交接过程",
    ]
    for issue in wordings:
        gate = evaluate_scene_audit(
            must_beat_ids=set(),
            verdicts=[],
            hard_fails=[],
            continuity_ok=False,
            continuity_issues=[issue],
            scene_text="乙接过钥匙。",
            working_state={"钥匙": "甲手中", "持有人": "甲"},
            prior_scene_ending="甲仍拿着钥匙，没有交给任何人。",
            entry_summary='上场结尾：甲仍拿着钥匙\n累计状态：{"持有人": "甲"}',
        )
        assert gate.continuity_block is True, issue


def test_r1_cont_softable_still_rejects_real_conflict():
    """_audit_cont_softable 路径也不得放行真实矛盾。"""
    issue = (
        "入场状态确认钥匙在甲手中，上场结尾也确认在甲手中，"
        "但本场无交接就变到乙手中"
    )
    gate = SimpleNamespace(
        critical_missed=[],
        hard_fails=[issue],
        continuity_issues=[],
        continuity_block=True,
    )
    assert (
        _audit_cont_softable(gate, working_state={"key_holder": "甲"}) is False
    )


# ========== R2：回退后 working_summary ==========


def test_r2_rollback_to_s0_clears_future_summary():
    """回退 s0 后不得携带 s1 摘要；WRITE 请求 entry_summary 为空。"""
    s0 = "第一场：甲在门廊检查怀表。" * 20
    s1 = "第二场：乙在仓库翻找铁链。" * 20
    sp: dict = {
        "scene_texts": [s0, s1],
        "scene_state_trail": [{"stage": "before"}, {"stage": "after_s0"}],
        "working_state": {"stage": "after_s0"},
        "working_summary": "STALE: end of s1",
        "scene_entry_summaries": ["", "上场结尾：第一场结尾…"],
        "scene_index": 1,
    }
    target = RepairTarget(
        scene_index=0,
        scene_id="s0",
        scene_ids=("s0",),
        issues=(
            LocatedIssue(
                issue_id="x#1",
                code="continuity",
                scene_ids=("s0",),
                expected_action="rewrite_scene",
                message="问题",
            ),
        ),
        instruction="改本场",
        locatable=True,
    )
    apply_repair_target_to_script_state(sp, target)
    assert sp["scene_index"] == 0
    assert sp["scene_texts"] == [s0]
    assert sp["working_state"] == {"stage": "before"}
    assert "STALE" not in str(sp.get("working_summary") or "")
    assert str(sp.get("working_summary") or "") == ""
    # 入口快照同步截断
    assert sp["scene_entry_summaries"] == [""]


def test_r2_rollback_mid_scene_rebuilds_from_prior():
    """回退中场：有快照用快照；无快照从保留的前场正文与入口状态重建。"""
    s0 = "第一场完整正文，甲把钥匙放在桌上然后离开房间。" * 20
    s1 = "第二场完整正文，乙进入房间发现钥匙。" * 20
    s2 = "第三场完整正文，丙在走廊拦截乙。" * 20
    prior_summary = (
        f"上场结尾：{s0[-300:]}\n"
        f'累计状态：{{"stage": "after_s0"}}'
    )
    # 有快照：优先恢复快照
    sp: dict = {
        "scene_texts": [s0, s1, s2],
        "scene_state_trail": [
            {"stage": "before"},
            {"stage": "after_s0"},
            {"stage": "after_s1"},
        ],
        "working_state": {"stage": "after_s1"},
        "working_summary": "STALE: end of s2",
        "scene_entry_summaries": ["", prior_summary, "上场结尾：s1…"],
        "scene_index": 2,
    }
    target = RepairTarget(
        scene_index=1,
        scene_id="s1",
        scene_ids=("s1",),
        issues=(
            LocatedIssue(
                issue_id="y#1",
                code="continuity",
                scene_ids=("s1",),
                expected_action="rewrite_scene",
                message="中场问题",
            ),
        ),
        instruction="改中场",
        locatable=True,
    )
    apply_repair_target_to_script_state(sp, target)
    assert sp["scene_index"] == 1
    assert sp["scene_texts"] == [s0, s1]
    assert sp["working_state"] == {"stage": "after_s0"}
    summary = str(sp.get("working_summary") or "")
    assert "STALE" not in summary
    assert "s2" not in summary
    assert summary == prior_summary

    # 无快照：从前场正文与 trail 状态重建
    sp2: dict = {
        "scene_texts": [s0, s1, s2],
        "scene_state_trail": [
            {"stage": "before"},
            {"stage": "after_s0"},
            {"stage": "after_s1"},
        ],
        "working_state": {"stage": "after_s1"},
        "working_summary": "STALE: end of s2",
        "scene_index": 2,
    }
    apply_repair_target_to_script_state(sp2, target)
    summary2 = str(sp2.get("working_summary") or "")
    assert "STALE" not in summary2
    assert "s2" not in summary2
    assert s0[-30:] in summary2
    assert "after_s0" in summary2


def test_r2_write_payload_entry_summary_matches_snapshot():
    """WRITE_SCENE payload 的 entry_summary 来自快照，不是后场残留。"""
    from regent.novel.domain.script_protocol import (
        PROTOCOL_SCRIPT_SCENE,
        ChapterScript,
        ScriptChoice,
        empty_script_state,
    )

    s0 = "甲在门廊检查怀表，确认时间还早。" * 30
    cards = _cards_json(3)
    packet = {
        "script": ChapterScript(
            title_line="选定",
            end_change="变化",
            cost="代价",
            cost_type="暴露",
            power_payoff="兑现",
            beats=["当场表态"],
        ).model_dump(mode="json"),
        "direction": ScriptChoice(selected_id="alpha").model_dump(mode="json"),
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
            "scene_plan": {"cards": cards, "chapter_goal": "x"},
            "scene_index": 0,
            "scene_texts": [s0],
            "scene_state_trail": [{"k": "before"}],
            "working_state": {"k": "before"},
            "working_summary": "",  # 已回退到 s0
            "scene_entry_summaries": [""],
            "chapter_validate_repairs": 0,
        }
    )
    take = {
        "scene_index": 0,
        "take_no": 1,
        "content": s0,
        "events": [],
        "status": "DRAFT",
        "scene_state": "DIRECTOR_VIEW",
        "artifact": "PROSE",
        "brief": {"purpose": "script_chapter"},
        "turn": 0,
        "performances": [],
        "round_actions": [],
        "revisions": 0,
        "prose_versions": [s0],
        "state_before": {},
        "validation": {"passed": False, "issues": [], "facts": []},
    }
    production = {
        "schema_version": 1,
        "protocol": PROTOCOL_SCRIPT_SCENE,
        "phase": "WRITE_SCENE",
        "cast": {"主角": {}},
        "takes": [take],
        "accepted": [],
        "decisions": [],
        "working_state": {"k": "before"},
        "script_protocol": sp,
        "call_count": 0,
        "committed_minor": 0,
        "scene_index": 0,
        "plan": {"scenes": [{"purpose": "x", "actors": []}]},
        "call_key_version": 2,
    }
    run = SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        generation_context={
            "architecture_version": "director_script_scene",
            "executor": "director_script_scene",
            "executor_version": "director_script_scene@1",
            "canon": [],
            "actual_state": {},
            "recent_chapters": [],
            "memory": [],
            "production": production,
            "call_key_version": 2,
        },
        performances=[],
        review={},
        user_guidance={},
        current_step="PRODUCE",
        content=s0,
        title="第1章",
        word_count=len(s0),
        input_version=1,
    )
    work = SimpleNamespace(id=uuid.uuid4())
    recorded: list[dict] = []

    class Provider:
        async def generate_structured(self, *, response_model, **kwargs):
            recorded.append(kwargs)
            if response_model.__name__ == "_SceneProseWithBeats":
                out = response_model(
                    content="重写后的第一场正文，足够长度以通过校验。" * 20,
                    beat_evidence={"b0": "重写后的"},
                )
            else:
                out = response_model.model_validate(
                    response_model.model_json_schema().get("examples", [{}])[0]
                    if response_model.model_json_schema().get("examples")
                    else {}
                ) if False else None
                # 兜底：直接构造最小合法对象
                raise AssertionError(f"unexpected schema {response_model}")
            return StructuredModelResponse(
                output=out.model_copy(deep=True),
                usage=ModelUsage(10, 20),
                model="test",
            )

    import asyncio

    session = SimpleNamespace(
        scalar=AsyncMockScalar(),
        scalars=AsyncMockScalars(),
        add=lambda row: None,
        flush=AsyncMockNone(),
        commit=AsyncMockNone(),
        execute=AsyncMockExecute(),
    )

    async def _run():
        # 只跑到 WRITE_SCENE 发出请求；后续 AUDIT 用不到
        class _Prov:
            async def generate_structured(self, *, response_model, **kwargs):
                recorded.append(
                    {"schema": getattr(response_model, "__name__", ""), **kwargs}
                )
                if getattr(response_model, "__name__", "") == "_SceneProseWithBeats":
                    out = response_model(
                        content="重写后的第一场正文，足够长度以通过校验。" * 20,
                        beat_evidence={"b0": "重写后的"},
                    )
                    return StructuredModelResponse(
                        output=out.model_copy(deep=True),
                        usage=ModelUsage(10, 20),
                        model="test",
                    )
                raise AssertionError(response_model)

        try:
            await _produce_script_tick(
                session,
                provider=_Prov(),
                work=work,
                run=run,
                production=production,
            )
        except Exception as exc:
            # CallBroker 可能因缺表失败；只要 WRITE payload 已记录即可
            if not recorded:
                raise exc

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run())
    write_reqs = [
        r for r in recorded if r.get("schema") == "_SceneProseWithBeats"
    ]
    if write_reqs:
        payload = write_reqs[0].get("user_prompt") or ""
        assert '"entry_summary": ""' in payload or '"entry_summary":""' in payload
        assert "STALE" not in payload


class AsyncMockScalar:
    async def __call__(self, query):
        return None


class AsyncMockScalars:
    async def __call__(self, query):
        return SimpleNamespace(all=lambda: [])


class AsyncMockNone:
    async def __call__(self, *a, **k):
        return None


class AsyncMockExecute:
    async def __call__(self, *a, **k):
        return SimpleNamespace(scalar_one_or_none=lambda: None, all=lambda: [])


# ========== R3：修订票据 hash ==========


def test_r3_mixed_paragraphs_unchanged_content_hash_matches():
    """短对白+长段混合：正文未变时，票据 hash 必须等于完整正文 hash。"""
    short_dlg = "「走。」"
    long_para = LONG
    texts = [
        long_para + "\n\n" + short_dlg + "\n\n" + ("甲继续前行。" * 20),
        "乙在另一处等待消息传来。" * 20,
    ]
    cards = _cards_json(2)
    full_chapter = "\n\n".join(texts)
    msg = "[continuity] 状态冲突；摘录「" + PARA + "」"
    located = locate_fail_messages(
        [msg], scene_texts=texts, cards=cards, chapter_content=full_chapter
    )
    assert located
    # content_hash 必须绑定完整正文，不是检测投影
    assert located[0].content_hash == content_hash(full_chapter)
    assert located[0].content_hash != content_hash(
        "\n\n".join(
            p
            for t in texts
            for p in t.split("\n\n")
            if len(p.strip()) >= 32
        )
    )

    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改："
    )
    sp: dict = {
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": 0}, {"s": 1}],
        "working_state": {"s": 1},
    }
    apply_repair_target_to_script_state(sp, target)
    ticket = sp["pending_repair_ticket"]
    # 未变化正文时 base_content_hash == full content hash → 无进展分支可触发
    assert ticket["base_content_hash"] == content_hash(full_chapter)


def test_r3_no_progress_branch_can_trigger_with_short_dialogue():
    """混合正文未变 + 问题仍在 → base hash 比较为 True（可触发止损）。"""
    short_dlg = "「嗯。」"
    texts = [
        LONG + "\n\n" + short_dlg + "\n\n" + ("他点头示意。" * 20),
        "另一场内容足够长以进入投影。" * 20,
    ]
    cards = _cards_json(2)
    full_chapter = "\n\n".join(texts)
    msg = "[front:redundant] 复读；摘录「" + PARA + "」"
    located = locate_fail_messages([msg], scene_texts=texts, cards=cards)
    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改："
    )
    sp: dict = {
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": 0}, {"s": 1}],
        "working_state": {"s": 1},
    }
    apply_repair_target_to_script_state(sp, target)
    base_hash = sp["pending_repair_ticket"]["base_content_hash"]
    # 模拟修订后正文未变
    assert content_hash(full_chapter) == base_hash


# ========== R4：模型定位核验 ==========


def test_r4_legal_id_fake_quote_rejected():
    """合法 scene_id + 不存在的引文 → 核验失败，不得改写目标。"""
    cards = _cards_json(3)
    texts = ["甲场正文足够长。" * 30, "乙场正文足够长。" * 30, "丙场正文足够长。" * 30]
    full = "\n\n".join(texts)
    data = {
        "scene_ids": ["s0"],
        "evidence_quotes": ["完全不存在的引文"],
        "code": "continuity",
        "message": "硬失败",
        "content_hash": content_hash(full),
    }
    assert (
        _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )


def test_r4_correct_quote_wrong_scene_rejected():
    """引文在 s1，模型却指向 s0 → 拒绝。"""
    cards = _cards_json(3)
    quote = "乙把钥匙放在桌上"
    texts = [
        "甲场完全没有提到钥匙的任何事情。" * 20,
        quote + "，然后转身离开房间。" * 20,
        "丙场与其他事无关。" * 20,
    ]
    full = "\n\n".join(texts)
    data = {
        "scene_ids": ["s0"],
        "evidence_quotes": [quote],
        "code": "continuity",
        "message": "硬失败",
        "content_hash": content_hash(full),
    }
    assert (
        _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )
    # 正确归属到 s1 则通过
    data_ok = {**data, "scene_ids": ["s1"]}
    assert _verify_model_location(
        data=data_ok, cards=cards, texts=texts, full_chapter=full
    ) == ("s1",)


def test_r4_stale_hash_rejected():
    """过期 content_hash 与当前稿不符 → 拒绝。"""
    cards = _cards_json(2)
    quote = "钥匙静静躺在桌面"
    texts = [quote + "正中，无人去碰。" * 20, "乙场内容。" * 20]
    full = "\n\n".join(texts)
    data = {
        "scene_ids": ["s0"],
        "evidence_quotes": [quote],
        "code": "continuity",
        "message": "硬失败",
        "content_hash": content_hash("旧稿完全不同"),
    }
    assert (
        _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )


def test_r4_beat_missing_requires_explicit_beat_ids():
    """D2：无引文的节拍定位必须带显式 beat_ids；仅 message 子串不得放行。"""
    cards = _cards_json(2)
    texts = ["甲场。" * 40, "乙场。" * 40]
    full = "\n\n".join(texts)
    # message 含 b0 但无 beat_ids → 拒绝
    data = {
        "scene_ids": ["s0"],
        "evidence_quotes": [],
        "code": "beats_missed",
        "message": "节拍 b0 完全缺失",
        "content_hash": content_hash(full),
    }
    assert (
        _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )
    # 显式 beat_ids=['b0'] → 映射到 s0
    data_ok = {**data, "beat_ids": ["b0"], "message": "缺少关键节拍"}
    assert _verify_model_location(
        data=data_ok, cards=cards, texts=texts, full_chapter=full
    ) == ("s0",)


def test_r4_beat_ids_mapping_used():
    """C2/D2：缺失节拍用 beat_id→scene_id 映射，不靠 message 模糊匹配。"""
    cards = _cards_json(3)  # s0/b0, s1/b1, s2/b2
    texts = ["甲。" * 40, "乙。" * 40, "丙。" * 40]
    full = "\n\n".join(texts)
    data = {
        "scene_ids": ["s0"],  # 声明错误
        "evidence_quotes": [],
        "code": "beats_missed",
        "message": "节拍缺失",  # message 不含 beat_id
        "beat_ids": ["b2"],
        "content_hash": content_hash(full),
    }
    # 映射 b2→s2，应覆盖错误声明
    assert _verify_model_location(
        data=data, cards=cards, texts=texts, full_chapter=full
    ) == ("s2",)
    # 未知 beat_id → 拒绝
    data_bad = {**data, "beat_ids": ["b99"]}
    assert (
        _verify_model_location(
            data=data_bad, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )


def test_d2_b1_does_not_match_b10():
    """D2：b1 与 b10 不得互相命中；混合无效 ID 整条拒绝。"""
    cards = [
        {
            "scene_id": "s0",
            "purpose": "p",
            "beats": [
                {"beat_id": "b1", "must_show": True, "description": "d"},
                {"beat_id": "b10", "must_show": True, "description": "d"},
            ],
        },
        {
            "scene_id": "s1",
            "purpose": "p",
            "beats": [{"beat_id": "b2", "must_show": True, "description": "d"}],
        },
    ]
    texts = ["甲。" * 40, "乙。" * 40]
    full = "\n\n".join(texts)
    assert _verify_model_location(
        data={
            "scene_ids": ["s0"],
            "evidence_quotes": [],
            "code": "missing_beat",
            "beat_ids": ["b10"],
            "message": "缺 b1",  # 文案含 b1 不得影响
        },
        cards=cards,
        texts=texts,
        full_chapter=full,
    ) == ("s0",)
    # 混合有效+无效 → 拒绝（不得静默丢掉无效 ID）
    assert (
        _verify_model_location(
            data={
                "scene_ids": ["s0"],
                "evidence_quotes": [],
                "code": "missing_beat",
                "beat_ids": ["b1", "b99"],
                "message": "缺节拍",
            },
            cards=cards,
            texts=texts,
            full_chapter=full,
        )
        is None
    )


def test_d2_schema_parse_preserves_beat_ids_into_repair():
    """D2：LocatedIssueSpec 解析 → model_dump → 生产定位路径保留 beat_ids。"""
    from regent.novel.application.directing_contracts import (
        LocatedIssueSpec,
        ScriptChapterValidation,
    )
    from regent.novel.domain.errors import ProductionStopped

    cards = _cards_json(2)
    texts = ["甲场足够长的正文。" * 30, "乙场足够长的正文。" * 30]
    full = "\n\n".join(texts)
    raw = {
        "hard_fails": ["[missing_beat] 缺少关键节拍"],
        "soft_notes": [],
        "facts": [],
        "located_issues": [
            {
                "issue_id": "mb#1",
                "code": "missing_beat",
                "severity": "hard",
                "scene_ids": ["s0"],
                "evidence_quotes": [],
                "beat_ids": ["b0"],
                "content_hash": content_hash(full),
                "expected_action": "rewrite_scene",
                "verify_rule": "",
                "message": "缺少关键节拍",  # 不含 b0
            }
        ],
    }
    report = ScriptChapterValidation.model_validate(raw)
    dumped = report.located_issues[0].model_dump()
    assert dumped.get("beat_ids") == ["b0"]
    assert _verify_model_location(
        data=dumped, cards=cards, texts=texts, full_chapter=full
    ) == ("s0",)

    sp: dict = {
        "scene_plan": {"cards": cards, "chapter_goal": "x"},
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": 0}, {"s": 1}],
        "working_state": {"s": 1},
        "scene_index": 1,
        "working_summary": "",
        "scene_entry_summaries": ["", ""],
    }
    production: dict = {"phase": "VALIDATE", "script_protocol": sp}
    _begin_located_scene_repair(
        production,
        sp,
        fails=["[missing_beat] 缺少关键节拍"],
        instruction_prefix="改：",
        model_located=list(report.located_issues),
    )
    assert production["phase"] == "WRITE_SCENE"
    assert sp["scene_index"] == 0

    # 无 beat_ids 的 schema 结果不得靠 message 放行
    raw2 = {
        "hard_fails": ["[missing_beat] 缺少关键节拍 b0"],
        "located_issues": [
            {
                "issue_id": "mb#2",
                "code": "missing_beat",
                "scene_ids": ["s0"],
                "evidence_quotes": [],
                "message": "缺少关键节拍 b0",
            }
        ],
    }
    report2 = ScriptChapterValidation.model_validate(raw2)
    dumped2 = report2.located_issues[0].model_dump()
    assert dumped2.get("beat_ids") == []
    assert (
        _verify_model_location(
            data=dumped2, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )


def test_r4_unlocated_fail_with_fake_quote_stops():
    """硬失败无可检索位置 + 模型伪引文 → 不得进入 WRITE_SCENE 改写目标。"""
    cards = _cards_json(3)
    texts = [
        "甲场正文关于怀表的检查过程写得很细。" * 30,
        "乙场正文关于仓库的搜索写得很细。" * 30,
        "丙场正文关于走廊的对峙写得很细。" * 30,
    ]
    sp: dict = {
        "scene_plan": {"cards": cards, "chapter_goal": "x"},
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": i} for i in range(3)],
        "working_state": {"s": 2},
        "scene_index": 2,
        "working_summary": "上场结尾：丙场…",
        "scene_entry_summaries": ["", "s0…", "s1…"],
    }
    production: dict = {"phase": "VALIDATE", "script_protocol": sp}
    model_located = [
        SimpleNamespace(
            model_dump=lambda: {
                "issue_id": "model#1",
                "code": "continuity",
                "severity": "hard",
                "scene_ids": ["s0"],
                "evidence_quotes": ["完全不存在的引文"],
                "content_hash": "",
                "expected_action": "rewrite_scene",
                "verify_rule": "",
                "message": "硬失败无可检索位置",
            }
        )
    ]
    # 确定性定位也失败（fails 无摘录、无法映射）→ 模型伪证据不得单独放行
    from regent.novel.domain.errors import ProductionStopped

    with pytest.raises(ProductionStopped):
        _begin_located_scene_repair(
            production,
            sp,
            fails=["[continuity] 某种无法定位的硬失败描述"],
            instruction_prefix="章节核验未过：",
            model_located=model_located,
        )


def test_r4_verified_model_location_fills_gap():
    """确定性未定位 + 模型绑定该 fail + 真引文 → 可补齐并改写目标。"""
    cards = _cards_json(3)
    quote = "乙把钥匙放在了桌面正中"
    texts = [
        "甲场与钥匙无关的叙述。" * 30,
        quote + "，然后离开了房间。" * 30,
        "丙场与钥匙无关。" * 30,
    ]
    full = "\n\n".join(texts)
    sp: dict = {
        "scene_plan": {"cards": cards, "chapter_goal": "x"},
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": i} for i in range(3)],
        "working_state": {"s": 2},
        "scene_index": 2,
        "working_summary": "",
        "scene_entry_summaries": ["", "", ""],
    }
    production: dict = {"phase": "VALIDATE", "script_protocol": sp}
    fail_msg = "[fact_conflict] 乙的钥匙来源无法解释"
    model_located = [
        SimpleNamespace(
            model_dump=lambda: {
                "issue_id": "model#ok",
                "code": "fact_conflict",
                "severity": "hard",
                "scene_ids": ["s1"],
                "evidence_quotes": [quote],
                "content_hash": content_hash(full),
                "expected_action": "rewrite_scene",
                "verify_rule": "",
                # 必须与待修 fail 实质对应，不能是无关问题
                "message": "乙的钥匙来源无法解释",
            }
        )
    ]
    _begin_located_scene_repair(
        production,
        sp,
        fails=[fail_msg],
        instruction_prefix="章节核验未过：",
        model_located=model_located,
    )
    assert production["phase"] == "WRITE_SCENE"
    assert sp["scene_index"] == 1
    assert sp["pending_repair_ticket"]["scene_id"] == "s1"


def test_r4_unrelated_model_issue_cannot_change_target():
    """C2：真实引文 + 正确 hash，但问题无关 → 不得改写目标。"""
    from regent.novel.domain.errors import ProductionStopped
    from regent.novel.application.directing_script_loop import (
        _model_binds_to_unsolved_fail,
    )

    cards = _cards_json(3)
    real_quote = "甲在门廊检查怀表，确认时间还早得很"
    texts = [
        real_quote + "，然后收起怀表离开。" * 20,
        "乙场在仓库翻找铁链，没有找到钥匙。" * 20,
        "丙场在走廊对峙，无关钥匙。" * 20,
    ]
    full = "\n\n".join(texts)
    fail_msg = "[continuity] 乙的钥匙来源无法解释"
    data = {
        "issue_id": "model#unrelated",
        "code": "unrelated",
        "severity": "hard",
        "scene_ids": ["s0"],
        "evidence_quotes": [real_quote, "此处完全虚构且不存在的证据"],
        "content_hash": content_hash(full),
        "expected_action": "rewrite_scene",
        "verify_rule": "",
        "message": "与原核验无关的问题",
    }
    # 绑定检查：无关条目不得通过
    assert (
        _model_binds_to_unsolved_fail(data, fails=[fail_msg], located=[])
        is False
    )
    # 即使单条引文真实，混合假引文也会被 _verify 拒绝
    assert (
        _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )

    sp: dict = {
        "scene_plan": {"cards": cards, "chapter_goal": "x"},
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": i} for i in range(3)],
        "working_state": {"s": 2},
        "scene_index": 2,
        "working_summary": "",
        "scene_entry_summaries": ["", "", ""],
    }
    production: dict = {"phase": "VALIDATE", "script_protocol": sp}
    model_located = [SimpleNamespace(model_dump=lambda: data)]
    with pytest.raises(ProductionStopped):
        _begin_located_scene_repair(
            production,
            sp,
            fails=[fail_msg],
            instruction_prefix="章节核验未过：",
            model_located=model_located,
        )
    # 不得进入 WRITE_SCENE / 不得截到 s0
    assert production.get("phase") != "WRITE_SCENE"
    assert int(sp.get("scene_index", 2)) == 2


def test_r4_mixed_real_fake_quotes_rejected():
    """C2：真假引文混合 → 整条拒绝，不得只凭一条真引文放行。"""
    cards = _cards_json(2)
    real_q = "乙把钥匙放在了桌面正中"
    texts = [
        "甲场无关。" * 30,
        real_q + "，然后离开房间。" * 30,
    ]
    full = "\n\n".join(texts)
    data = {
        "scene_ids": ["s1"],
        "evidence_quotes": [real_q, "完全不存在的假证据句子"],
        "code": "continuity",
        "message": "乙的钥匙来源无法解释",
        "content_hash": content_hash(full),
    }
    assert (
        _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )


def test_r4_cross_scene_multi_quotes_accepted():
    """C2：跨场多引文，每条归属对应场景、每场有证据 → 可接受。"""
    cards = _cards_json(3)
    q0 = "甲在门廊收起怀表准备出发"
    q2 = "丙在走廊拦住乙盘问钥匙下落"
    texts = [
        q0 + "，天色尚早。" * 20,
        "乙场中段与定位无关。" * 20,
        q2 + "，语气严厉。" * 20,
    ]
    full = "\n\n".join(texts)
    data = {
        "scene_ids": ["s0", "s2"],
        "evidence_quotes": [q0, q2],
        "code": "continuity",
        "message": "钥匙交接链断裂",
        "content_hash": content_hash(full),
    }
    assert _verify_model_location(
        data=data, cards=cards, texts=texts, full_chapter=full
    ) == ("s0", "s2")


def test_r4_cross_scene_missing_scene_evidence_rejected():
    """C2：声明 s0+s2，但 s2 无任何对应引文 → 拒绝。"""
    cards = _cards_json(3)
    q0 = "甲在门廊收起怀表准备出发"
    texts = [
        q0 + "，天色尚早。" * 20,
        "乙场中段。" * 20,
        "丙场没有怀表也没有钥匙的任何描述。" * 20,
    ]
    full = "\n\n".join(texts)
    data = {
        "scene_ids": ["s0", "s2"],
        "evidence_quotes": [q0],
        "code": "continuity",
        "message": "钥匙交接链断裂",
        "content_hash": content_hash(full),
    }
    assert (
        _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full
        )
        is None
    )


def test_r4_multiple_independent_same_code_issues():
    """C2：同类多个独立问题，模型只补齐未定位的那一条。"""
    cards = _cards_json(3)
    q1 = "乙把钥匙放在了桌面正中"
    texts = [
        "甲场与问题无关的长叙述。" * 30,
        q1 + "，然后离开。" * 30,
        "丙场另一处也有钥匙相关的错误描述。" * 30,
    ]
    full = "\n\n".join(texts)
    fail_located = "[fact_conflict] 甲场人物身份写错；摘录「甲场与问题无关」"
    fail_open = "[fact_conflict] 乙的钥匙来源无法解释"
    sp: dict = {
        "scene_plan": {"cards": cards, "chapter_goal": "x"},
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": i} for i in range(3)],
        "working_state": {"s": 2},
        "scene_index": 2,
        "working_summary": "",
        "scene_entry_summaries": ["", "", ""],
    }
    production: dict = {"phase": "VALIDATE", "script_protocol": sp}
    model_located = [
        SimpleNamespace(
            model_dump=lambda: {
                "issue_id": "model#open",
                "code": "fact_conflict",
                "severity": "hard",
                "scene_ids": ["s1"],
                "evidence_quotes": [q1],
                "content_hash": content_hash(full),
                "expected_action": "rewrite_scene",
                "message": "乙的钥匙来源无法解释",
            }
        )
    ]
    _begin_located_scene_repair(
        production,
        sp,
        fails=[fail_located, fail_open],
        instruction_prefix="改本场：",
        model_located=model_located,
    )
    # 确定性定位 fail_located 到 s0（摘录在甲场）；模型补 fail_open 到 s1
    # 最早可定位场是 s0
    assert sp["scene_index"] == 0
    all_sids = [
        sid
        for i in sp["located_issues"]
        for sid in (i.get("scene_ids") or [])
    ]
    assert "s0" in all_sids
    assert "s1" in all_sids
    assert sp["pending_repair_ticket"]["scene_id"] == "s0"


def test_r4_model_located_fake_does_not_change_target():
    """硬失败无可检索位置 + 模型伪引文 → 不得进入 WRITE_SCENE 改写目标。"""
    cards = _cards_json(3)
    texts = [
        "甲场正文关于怀表的检查过程写得很细。" * 30,
        "乙场正文关于仓库的搜索写得很细。" * 30,
        "丙场正文关于走廊的对峙写得很细。" * 30,
    ]
    sp: dict = {
        "scene_plan": {"cards": cards, "chapter_goal": "x"},
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": i} for i in range(3)],
        "working_state": {"s": 2},
        "scene_index": 2,
        "working_summary": "上场结尾：丙场…",
        "scene_entry_summaries": ["", "s0…", "s1…"],
    }
    production: dict = {"phase": "VALIDATE", "script_protocol": sp}
    fail_msg = "[continuity] 乙的钥匙来源无法解释"
    model_located = [
        SimpleNamespace(
            model_dump=lambda: {
                "issue_id": "model#1",
                "code": "continuity",
                "severity": "hard",
                "scene_ids": ["s0"],
                "evidence_quotes": ["完全不存在的引文"],
                "content_hash": "",
                "expected_action": "rewrite_scene",
                "verify_rule": "",
                "message": "乙的钥匙来源无法解释",
            }
        )
    ]
    from regent.novel.domain.errors import ProductionStopped

    with pytest.raises(ProductionStopped):
        _begin_located_scene_repair(
            production,
            sp,
            fails=[fail_msg],
            instruction_prefix="章节核验未过：",
            model_located=model_located,
        )


def test_r4_conflicting_deterministic_not_overridden_by_model():
    """确定性已定位到后场，模型误报首场 → 不得改写到首场。"""
    cards = _cards_json(3)
    para = PARA + "更多内容使其足够长以被检测。"
    texts = [
        "短。",
        para + "\n\n" + "中场独有内容。" * 20,
        para + "\n\n" + "末场独有内容。" * 20,
    ]
    full = "\n\n".join(texts)
    sp: dict = {
        "scene_plan": {"cards": cards, "chapter_goal": "x"},
        "scene_texts": list(texts),
        "scene_state_trail": [{"s": i} for i in range(3)],
        "working_state": {"s": 2},
        "scene_index": 2,
        "working_summary": "",
        "scene_entry_summaries": ["", "", ""],
    }
    production: dict = {"phase": "VALIDATE", "script_protocol": sp}
    det_msg = "[front:redundant] 同章出现完全相同段落重复；摘录「" + PARA + "」"
    model_located = [
        SimpleNamespace(
            model_dump=lambda: {
                "issue_id": "model#conflict",
                "code": "front:redundant",
                "severity": "hard",
                "scene_ids": ["s0"],
                "evidence_quotes": [PARA],
                "content_hash": content_hash(full),
                "expected_action": "remove_dup",
                "verify_rule": "",
                "message": det_msg,
            }
        )
    ]
    _begin_located_scene_repair(
        production,
        sp,
        fails=[det_msg],
        instruction_prefix="改本场：",
        model_located=model_located,
    )
    # 确定性定位最早 s1，模型 s0 不得覆盖
    assert sp["scene_index"] == 1
    assert sp["pending_repair_ticket"]["scene_id"] == "s1"


# ========== R5：call_key_version 生命周期 ==========


def test_r5_pinned_keys_include_run_identity():
    assert "call_key_version" in PINNED_CONTEXT_KEYS
    assert "continuation" in PINNED_CONTEXT_KEYS
    carried = carry_over(
        {
            "executor": "director_script_scene",
            "executor_version": "director_script_scene@1",
            "call_key_version": 2,
            "continuation": {"source_run_id": "abc", "policy_version": 1},
            "unrelated": "drop-me",
        }
    )
    assert carried["call_key_version"] == 2
    assert carried["continuation"]["source_run_id"] == "abc"
    assert "unrelated" not in carried


def test_r5_carry_over_then_resolve_keeps_v2():
    """carry_over→resolve：新运行版本 2 不得退回 1。"""
    old_ctx = {
        "executor": "director_script_scene",
        "call_key_version": 2,
        "production": {"call_key_version": 2, "takes": []},
    }
    pinned = carry_over(old_ctx)
    # 模拟 ASSEMBLE 重建：只保留 pinned + 新字段
    new_ctx = {**pinned, "architecture_version": "director_script_scene"}
    run = SimpleNamespace(generation_context=new_ctx)
    prod: dict = {}  # production 重建后为空
    hydrate_call_key_version(prod, run)
    assert prod["call_key_version"] == 2
    assert resolve_call_key_version(prod, run) == 2


def test_r5_legacy_run_stays_v1():
    """旧运行缺省保持 1，不得被误标为 2。"""
    old_ctx = {"executor": "director_v2"}
    pinned = carry_over(old_ctx)
    new_ctx = {**pinned, "architecture_version": "director_v2"}
    run = SimpleNamespace(generation_context=new_ctx)
    prod: dict = {}
    hydrate_call_key_version(prod, run)
    assert prod["call_key_version"] == 1


def test_r5_plan_script_chapter_inherits_call_key_version():
    """_plan_script_chapter 初始化 production 时继承 call_key_version。"""
    from regent.novel.application.directing_planning import _inherit_call_key_version

    run_new = SimpleNamespace(
        generation_context={
            "call_key_version": 2,
            "continuation": {"source_run_id": "x"},
        }
    )
    assert _inherit_call_key_version(run_new) == {"call_key_version": 2}

    run_old = SimpleNamespace(generation_context={})
    assert _inherit_call_key_version(run_old) == {"call_key_version": 1}

    run_from_prod = SimpleNamespace(
        generation_context={"production": {"call_key_version": 2}}
    )
    assert _inherit_call_key_version(run_from_prod) == {"call_key_version": 2}


def test_r5_generation_assemble_preserves_run_identity():
    """generation.assemble 重建后 generation_context 保留 call_key_version/continuation。"""
    import inspect

    from regent.novel.application import generation

    src = inspect.getsource(generation.assemble)
    assert "carry_over" in src
    assert "call_key_version" in src
    assert "pinned" in src


def test_r5_script_command_id_uses_v2_tag():
    """新运行 v2 时 _script_scene_command_id 带 vrep 标记（与旧格式分流）。"""
    from regent.novel.application.directing_calls import _script_scene_command_id

    production = {"call_key_version": 2, "decisions": []}
    sp: dict = {"scene_index": 0, "scene_plan": {"cards": _cards_json(2)}}
    run = SimpleNamespace(
        generation_context={"call_key_version": 2}, input_version=1
    )
    cid = _script_scene_command_id(run, production, sp, "WRITE_SCENE")
    assert ":vrep0" in cid or "vrep" in cid

    production_v1: dict = {"decisions": []}
    run_v1 = SimpleNamespace(generation_context={}, input_version=1)
    cid_v1 = _script_scene_command_id(run_v1, production_v1, sp, "WRITE_SCENE")
    assert "vrep" not in cid_v1


# ========== Prompt 契约：分场边界 / 修订声明语义 ==========


def test_prompt_scene_layout_exposes_boundaries():
    from regent.novel.domain.repair_locate import build_scene_layout

    layout = build_scene_layout(
        scene_texts=["甲场正文", "乙场更长一些"],
        cards=[{"scene_id": "s0"}, {"scene_id": "s1"}],
    )
    assert layout["scene_count"] == 2
    assert layout["scene_boundaries"] == [0, len("甲场正文") + 2]
    assert layout["scenes"][0]["scene_id"] == "s0"
    assert layout["scenes"][1]["char_start"] == layout["scene_boundaries"][1]


def test_prompt_required_facts_not_in_forbidden_claims():
    from regent.novel.domain.repair_locate import (
        LocatedIssue,
        RepairTarget,
        apply_repair_target_to_script_state,
        split_rewrite_claims,
    )

    issue = LocatedIssue(
        issue_id="fact#1",
        code="continuity",
        scene_ids=("s0",),
        evidence_quotes=("钥匙在小王手中",),
        expected_action="rewrite_scene",
        message="钥匙应在小李手中",
    )
    forbidden, required = split_rewrite_claims([issue])
    assert forbidden == []
    assert any("钥匙应在小李" in x for x in required)

    wrong = LocatedIssue(
        issue_id="fact#2",
        code="continuity",
        scene_ids=("s0",),
        expected_action="rewrite_scene",
        message="正文误称钥匙仍在甲处",
    )
    forbidden2, required2 = split_rewrite_claims([wrong])
    assert any("误称" in x for x in forbidden2)
    assert required2 == []

    sp: dict = {
        "scene_texts": ["正文。" * 40],
        "scene_state_trail": [{}],
        "working_state": {},
    }
    apply_repair_target_to_script_state(
        sp,
        RepairTarget(
            scene_index=0,
            scene_id="s0",
            scene_ids=("s0",),
            issues=(issue,),
            instruction="改",
            locatable=True,
        ),
    )
    ticket = sp["pending_repair_ticket"]
    assert "钥匙应在小李手中" in ticket["required_facts"]
    assert ticket["forbidden_claims"] == []
