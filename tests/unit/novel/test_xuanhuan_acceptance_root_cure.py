"""玄幻失败根治：类型化验收契约、计划期责任场次、核验口径、失败态投影。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from regent.novel.domain.script_protocol import (
    AcceptanceItem,
    ChapterScript,
    ScriptChoice,
    acceptance_plan_issues,
    assign_acceptance_to_scenes,
    classify_acceptance_kind,
    derive_acceptance_items,
    normalize_chapter_validation_fails,
)
from regent.novel.domain.scene_card import SceneCard, StructuredBeat, compile_scene_plan

XUANHUAN_END = (
    "陆沉从单纯挖矿换粥的矿奴转变为持有《残生录》第一页、"
    "右眼能看见禁制暗纹的淬体境初窥者；"
    "并必须决定是否信任阿枝，而信任意味着把禁制感知的秘密分给她；"
    "他知道自己的秘密已经露出一角"
)


def _script(end: str = XUANHUAN_END) -> ChapterScript:
    return ChapterScript(
        title_line="矿奴",
        end_change=end,
        cost="代价",
        cost_type="暴露",
        power_payoff="看见禁制",
        turning_point="捡到储物戒",
        beats=["捡到戒", "手记显字"],
        protagonist_want="活下去",
        opposition="矿场与禁制",
    )


def _card(sid: str, purpose: str, beats: list[str]) -> SceneCard:
    return SceneCard(
        scene_id=sid,
        purpose=purpose,
        setting="矿脉",
        entry_state="矿奴",
        protagonist_objective="求生",
        opposition="禁制",
        core_choice="用不用手记",
        emotion_arc="怕到试",
        exit_change=purpose,
        beats=[
            StructuredBeat(beat_id=f"{sid}b{i}", text=t, must_show=True)
            for i, t in enumerate(beats)
        ],
        target_chars=500,
    )


# --- kind 分类 ---


def test_classify_dilemma_vs_state():
    assert classify_acceptance_kind("必须决定是否信任阿枝") == "dilemma"
    assert classify_acceptance_kind("陆沉最终选择信任阿枝并说出秘密") == "state_change"
    assert classify_acceptance_kind("陆沉意识到秘密已经露出一角") == "cognition"
    assert classify_acceptance_kind("成为淬体境初窥者") == "state_change"
    assert classify_acceptance_kind("读者感到矿场压抑") == "display"


def test_derive_splits_compound_end_change():
    items = derive_acceptance_items(_script(), ScriptChoice(selected_id="alpha"))
    kinds = {i.kind for i in items}
    texts = "｜".join(i.text for i in items)
    assert "dilemma" in kinds
    assert "cognition" in kinds
    assert "state_change" in kinds
    # 复合终点被拆开，不再是一条 blob
    assert len([i for i in items if i.required]) >= 3
    assert "决定是否信任" in texts or "是否信任" in texts
    assert "秘密已经露出" in texts or "露出一角" in texts


def test_derive_prefers_director_acceptance_items():
    choice = ScriptChoice(
        selected_id="alpha",
        acceptance_items=[
            AcceptanceItem(
                item_id="a1",
                kind="state_change",
                text="持有第一页传承内容",
                min_evidence="掌心/记忆中有内容",
            ),
            AcceptanceItem(
                item_id="a2",
                kind="dilemma",
                text="必须决定是否信任阿枝",
                min_evidence="章末留下未解选择",
            ),
        ],
        must_land_beats=["读者感到危险"],
    )
    items = derive_acceptance_items(_script(), choice)
    assert [i.item_id for i in items][:2] == ["a1", "a2"]
    assert items[1].kind == "dilemma"
    assert items[1].required is True
    # must_land 作为 display 补齐，不硬拦
    assert any(i.kind == "display" and not i.required for i in items)
    assert any(i.item_id.startswith("ml") for i in items)


def test_director_display_item_forced_not_required():
    choice = ScriptChoice(
        selected_id="alpha",
        acceptance_items=[
            AcceptanceItem(item_id="d1", kind="display", text="气氛压抑", required=True)
        ],
    )
    items = derive_acceptance_items(_script("无关终点"), choice)
    assert items[0].required is False


# --- 计划期责任场次 ---


def test_acceptance_unassigned_is_hard_plan_issue():
    items = derive_acceptance_items(
        _script(),
        ScriptChoice(
            selected_id="alpha",
            acceptance_items=[
                AcceptanceItem(
                    item_id="a1", kind="state_change", text="成为淬体境初窥者"
                ),
                AcceptanceItem(
                    item_id="a2", kind="cognition", text="意识到秘密露出一角"
                ),
            ],
        ),
    )
    # 场景完全不覆盖义务
    cards = [
        _card("s1", "矿场日常", ["挖矿", "换粥"]).model_dump(mode="json"),
        _card("s2", "捡到戒指", ["摸到戒指"]).model_dump(mode="json"),
    ]
    assigned = assign_acceptance_to_scenes(items, cards)
    issues = acceptance_plan_issues(assigned)
    assert any(i.startswith("acceptance_unassigned:a1") for i in issues)
    assert any(i.startswith("acceptance_unassigned:a2") for i in issues)


def test_acceptance_assigned_when_scene_covers():
    items = [
        AcceptanceItem(
            item_id="a1",
            kind="state_change",
            text="成为淬体境初窥者",
        )
    ]
    cards = [
        _card("s2", "突破成为淬体境初窥者", ["矿渣淬体", "突破成功"]).model_dump(
            mode="json"
        )
    ]
    assigned = assign_acceptance_to_scenes(items, cards)
    assert assigned[0].scene_ids == ["s2"]
    assert acceptance_plan_issues(assigned) == []


def test_missing_acceptance_returns_to_director_without_mutating_last_scene():
    from regent.novel.domain.script_protocol import ensure_acceptance_assigned

    items = [
        AcceptanceItem(
            item_id="a1", kind="state_change", text="成为淬体境初窥者"
        ),
        AcceptanceItem(
            item_id="a2",
            kind="cognition",
            text="陆沉意识到自己的秘密已经露出一角",
        ),
    ]
    cards = [
        _card("s1", "矿场日常", ["挖矿", "换粥"]),
        _card("s2", "捡到戒指", ["摸到戒指"]),
    ]
    assigned, filled = ensure_acceptance_assigned(items, cards)
    assert len(acceptance_plan_issues(assigned)) == 2
    assert filled == cards
    assert [b.text for b in filled[-1].beats] == ["摸到戒指"]


def test_explicit_acceptance_owner_accepts_paraphrase_without_replaying_event():
    items = [AcceptanceItem(item_id="a1", kind="state_change", text="奖赏已发放")]
    first = _card("s1", "告密得赏", ["监工把肉塞进告密者手里"])
    first.acceptance_ids = ["a1"]
    later = _card("s2", "已到深夜", ["主角筹划明天"])
    later.entry_state = "奖赏已发放"
    assigned = assign_acceptance_to_scenes(items, [first, later])
    assert assigned[0].scene_ids == ["s1"]
    assert acceptance_plan_issues(assigned) == []
    later.acceptance_ids = ["a1"]
    assert any(x.startswith("acceptance_multiple_owners:") for x in
               acceptance_plan_issues(assign_acceptance_to_scenes(items, [first, later])))


def test_entry_state_is_not_acceptance_execution():
    item = AcceptanceItem(item_id="a1", kind="state_change", text="奖赏已经发放完毕")
    card = _card("s1", "已到深夜", ["主角筹划明天"])
    card.entry_state = item.text
    assert acceptance_plan_issues(assign_acceptance_to_scenes([item], [card]))


def test_compile_skeleton_injects_required_acceptance_beats():
    choice = ScriptChoice(
        selected_id="alpha",
        acceptance_items=[
            AcceptanceItem(
                item_id="a1", kind="state_change", text="成为淬体境初窥者"
            ),
            AcceptanceItem(
                item_id="a2",
                kind="cognition",
                text="陆沉意识到自己的秘密已经露出一角",
            ),
        ],
    )
    plan = compile_scene_plan(_script("挖矿求生"), choice, chapter_no=1)
    all_text = "｜".join(b.text for c in plan.cards for b in c.beats)
    assert "淬体境初窥者" in all_text
    assert "秘密已经露出" in all_text or "露出一角" in all_text


# --- 核验口径：去重 + dilemma 不误拦 ---


def test_normalize_merges_same_item_and_drops_dilemma_resolution():
    items = [
        AcceptanceItem(
            item_id="end1",
            kind="state_change",
            text="成为持有第一页、能看见禁制暗纹的淬体境初窥者",
        ),
        AcceptanceItem(
            item_id="end2",
            kind="dilemma",
            text="必须决定是否信任阿枝",
        ),
        AcceptanceItem(
            item_id="end3",
            kind="cognition",
            text="知道自己的秘密已经露出一角",
        ),
    ]
    fails = [
        # 同一 state_change 被拆成多条 → 应合并为一条
        "[acc:end1] 未持有第一页内容；摘录「纸页上只剩一片灰白」",
        "[acc:end1] 未成为淬体境初窥者；摘录「纸页上只剩一片灰白」",
        # dilemma 要求拍板 → 误拦，应丢弃
        "[acc:end2] 未做出是否信任阿枝的决定；摘录「给我留最底下那勺」",
        # dilemma 完全未建立 → 应保留
        # cognition 真实缺口 → 应保留
        "[acc:end3] 陆沉未意识到秘密已经露出一角；摘录「她看见了」",
        # 程序门禁标签原样保留
        "[front:redundant] 复读",
    ]
    out = normalize_chapter_validation_fails(fails, items)
    acc_fails = [f for f in out if f.startswith("[acc:")]
    assert len(acc_fails) == 2  # end1 合并为 1 + end3
    assert any("end1" in f for f in acc_fails)
    assert any("end3" in f for f in acc_fails)
    assert not any("end2" in f for f in acc_fails)
    assert any(f.startswith("[front:redundant]") for f in out)


def test_normalize_dilemma_not_established_kept():
    items = [
        AcceptanceItem(
            item_id="d1",
            kind="dilemma",
            text="必须决定是否信任阿枝",
        )
    ]
    fails = ["[acc:d1] 全章未建立是否信任阿枝的困境，没有任何选择张力"]
    out = normalize_chapter_validation_fails(fails, items)
    assert len(out) == 1


def test_normalize_display_not_hard():
    items = [
        AcceptanceItem(item_id="m1", kind="display", text="读者感到危险", required=False)
    ]
    fails = ["[acc:m1] 读者危险感不足"]
    assert normalize_chapter_validation_fails(fails, items) == []


def test_normalize_without_items_only_dedup():
    fails = ["问题A", "问题A", "问题B"]
    out = normalize_chapter_validation_fails(fails, [])
    assert out == ["问题A", "问题B"]


# --- 分环节：连续递延 / 画面硬停 ---


def test_audit_cont_deferrable_only_continuity():
    from regent.novel.application.directing_script_loop import (
        _append_gate_debt,
        _audit_cont_deferrable,
    )
    from regent.novel.domain.scene_card import SceneAuditDecision

    cont_only = SceneAuditDecision(
        critical_missed=[],
        evidence_fails=[],
        continuity_block=True,
        hard_fails=[],
        continuity_issues=["入场状态与正文矛盾"],
    )
    assert _audit_cont_deferrable(cont_only) is True

    with_critical = SceneAuditDecision(
        critical_missed=["b1"],
        evidence_fails=[],
        continuity_block=True,
        hard_fails=[],
        continuity_issues=["入场状态与正文矛盾"],
    )
    assert _audit_cont_deferrable(with_critical) is False

    with_hard = SceneAuditDecision(
        critical_missed=[],
        evidence_fails=[],
        continuity_block=True,
        hard_fails=["改因果终点"],
        continuity_issues=["入场状态与正文矛盾"],
    )
    assert _audit_cont_deferrable(with_hard) is False

    sp: dict = {}
    _append_gate_debt(sp, scene_id="s2", tags=["s2:cont:黑纹位置不一致"])
    assert sp["gate_debts"][0]["owner"] == "review"
    assert sp["gate_debts"][0]["kind"] == "continuity"
    assert "s2:cont:黑纹位置不一致" in sp["gate_debts"][0]["tags"]


def test_review_merge_and_coerce_deferred_continuity_only():
    from regent.novel.application.chapter_review import (
        _merge_gate_debts_into_issues,
        _only_deferred_issues,
    )

    sp = {
        "gate_debts": [
            {
                "owner": "review",
                "kind": "continuity",
                "scene_id": "s2",
                "tags": ["s2:cont:黑纹位置不一致"],
                "from_phase": "AUDIT_SCENE",
            }
        ]
    }
    merged = _merge_gate_debts_into_issues(["其它问题"], sp)
    assert any(x.startswith("[gate_debt:cont]") for x in merged)
    assert _only_deferred_issues(
        [x for x in merged if x.startswith("[gate_debt:cont]")]
    )
    assert _only_deferred_issues(["[front:redundant] 复读"]) is False


def test_prose_front_debt_merge_and_coerce_semantics():
    """prose_front 债务合并 + coerce 语义拆分：power_surface 可 coerce，redundant 不可。"""
    from regent.novel.application.chapter_review import (
        _merge_gate_debts_into_issues,
        _only_deferred_issues,
        _deferred_issues_contain_redundant,
    )
    from regent.novel.domain.prose_front_gates import review_issues_may_coerce

    sp = {
        "gate_debts": [
            {
                "owner": "review",
                "kind": "prose_front",
                "scene_id": "__chapter__",
                "tags": ["[front:power_surface] 缺金手指外显"],
                "from_phase": "VALIDATE",
            }
        ]
    }
    merged = _merge_gate_debts_into_issues([], sp)
    assert any("[gate_debt:prose_front]" in x and "power_surface" in x for x in merged)
    assert _only_deferred_issues(merged) is True
    assert _deferred_issues_contain_redundant(merged) is False
    assert review_issues_may_coerce(merged) is True

    sp_redundant = {
        "gate_debts": [
            {
                "owner": "review",
                "kind": "prose_front",
                "scene_id": "__chapter__",
                "tags": ["[front:redundant] 同章出现完全相同段落重复"],
                "from_phase": "VALIDATE",
            }
        ]
    }
    merged_r = _merge_gate_debts_into_issues([], sp_redundant)
    assert _only_deferred_issues(merged_r) is True
    assert _deferred_issues_contain_redundant(merged_r) is True
    assert review_issues_may_coerce(merged_r) is False


def test_append_gate_debt_prose_front_no_cont_pollution():
    """kind=prose_front 不写 audit_deferred_cont / scene_soft_fails。"""
    from regent.novel.application.directing_script_loop import _append_gate_debt

    sp: dict = {}
    _append_gate_debt(
        sp,
        scene_id="__chapter__",
        tags=["[front:redundant] 复读"],
        kind="prose_front",
        from_phase="VALIDATE",
    )
    assert sp["gate_debts"][0]["kind"] == "prose_front"
    assert "audit_deferred_cont" not in sp
    assert "scene_soft_fails" not in sp


def test_repaired_front_debt_is_revalidated_not_reintroduced():
    from regent.novel.application.chapter_review import _merge_gate_debts_into_issues

    old = "[front:redundant] 旧重复段"
    sp = {"gate_debts": [
        {"owner": "review", "kind": "prose_front", "tags": [old]},
        {"owner": "review", "kind": "continuity", "tags": ["持物冲突"]},
    ]}
    issues = _merge_gate_debts_into_issues([], sp, current_front_fails=[])
    assert issues == ["[gate_debt:cont]持物冲突"]
    assert sp["gate_debts"][0]["resolved_tags"] == [old]
    assert sp["gate_debts"][0]["tags"] == [old]  # 留存原始证据


def test_current_redundant_still_blocks_after_debt_revalidation():
    from regent.novel.application.chapter_review import _merge_gate_debts_into_issues
    from regent.novel.domain.prose_front_gates import review_issues_may_coerce

    current = "[front:redundant] 新正文仍然重复"
    sp = {"gate_debts": [
        {"owner": "review", "kind": "prose_front", "tags": ["[front:redundant] 旧摘录"]},
    ]}
    issues = _merge_gate_debts_into_issues([current], sp, current_front_fails=[current])
    assert issues == [current]
    assert not review_issues_may_coerce(issues)
    assert sp["gate_debts"][0]["resolved_tags"] == []


# --- 失败态投影 ---


def test_chapter_terminal_failed_overrides_writing_projection():
    from regent.novel.application.works_run_control import _progress_for_run
    from regent.novel.domain.states import ChapterRunState, StoryWorkState
    import asyncio

    step = SimpleNamespace(step="PRODUCE", state="FAILED")
    steps = SimpleNamespace(all=lambda: [step])
    run = SimpleNamespace(
        id=uuid.uuid4(),
        work_id=uuid.uuid4(),
        chapter_no=1,
        state=ChapterRunState.TERMINAL_FAILED.value,
        current_step="PRODUCE",
        version=3,
        auto_advance=True,
        generation_context={
            "production": {"last_error": "章节核验硬失败：未兑现剧本终点"}
        },
    )
    work = SimpleNamespace(state=StoryWorkState.RUNNING.value)

    class Session:
        async def scalars(self, q):
            return steps

        async def get(self, *a, **k):
            return work

    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _progress_for_run(Session(), run=run, work=work)
    )
    assert out.public_stage == "chapter_failed"
    assert "失败" in (out.stage_label or "")
    assert out.failure_reason and "未兑现" in out.failure_reason
    assert "retry_chapter" in out.available_actions
    assert out.work_state == "RUNNING"  # 作品层仍可 RUNNING，但展示以章失败为准
