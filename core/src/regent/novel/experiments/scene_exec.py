"""场景逐场演绎协议 X：选本 → 导演分场 → 逐场执笔 → 场记验收 → 整章组装。

与协议 S 的关键差异：

- 不再整章一次执笔；每场单独生成，节拍有稳定 ID 可验收。
- 场记一次调用同时完成：节拍落地判定、连续性核验、实际变化提取。
  修改后重新场记；未解决问题不会被后续步骤覆盖。
- 下一场承接核验后的临时状态（working_state + 上场结尾），全章通过后才提交正式事实。
- 分场计划做完整性校验：超限退回、重复 ID 阻断、关键节拍遗漏阻断。

本协议注册到 experiments 的 ``_RUNNERS``，可与 S 做同条件对照。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from regent.novel.domain.scene_card import (
    BeatVerdict,
    ChapterScenePlan,
    SceneCard,
    SCENE_WRITE_SYSTEM,
    assemble_chapter,
    compile_scene_plan,
    evaluate_scene_audit,
    must_land_ids,
    normalize_opening_must_show,
    validate_scene_plan,
)
from regent.novel.domain.script_protocol import (
    PROTOCOL_SCRIPT_SCENE,
    ChapterScript,
    ScriptChoice,
)
from regent.novel.experiments.quality_ab import (
    ChapterAccept,
    MeteredProvider,
    ProseFacts,
    SampleResult,
    SceneProse,
    ValidateReport,
    _fragment_payload,
    _finalize_result,
    _prose_too_short,
    BudgetExhausted,
    DENSITY_CONTRACT,
    IMMERSION_CONTRACT,
    MIN_PROSE_CHARS,
    OPENING_CONTRACT,
    SCALE_CONTRACT,
    TARGET_PROSE_CHARS,
    WEB_HOOK_CONTRACT,
)


# ---------------------------------------------------------------------------
# 协议专用模型
# ---------------------------------------------------------------------------


class ScenePlanDraft(BaseModel):
    """导演分场产物：把选定剧本编译成 2–4 张可执行场景卡。"""

    cards: list[SceneCard] = Field(min_length=1, max_length=6)
    chapter_goal: str = ""
    continuity_notes: str = ""


class SceneProseWithBeats(BaseModel):
    """单场执笔：正文 + 节拍证据。"""

    content: str = Field(min_length=50)
    beat_evidence: dict[str, str] = Field(
        default_factory=dict,
        description="beat_id → 正文证据摘录（可短，但必须在 content 中）",
    )


class SceneAudit(BaseModel):
    """场记一次调用的完整产物：节拍判定 + 连续性 + 实际变化。

    合并原先的 SceneSupervisorReport 与 SceneDeltas：两者分次调用会互相矛盾，
    且增加费用。程序检查完整性；修改后必须重新场记。
    """

    scene_id: str = Field(min_length=1)
    # 节拍
    beat_verdicts: list[BeatVerdict] = Field(default_factory=list)
    # 连续性
    continuity_ok: bool = True
    continuity_issues: list[str] = Field(default_factory=list)
    hard_fails: list[str] = Field(default_factory=list)
    # 实际变化（计划中的不算）
    facts: list[str] = Field(default_factory=list, description="已发生的不可逆事实")
    state_changes: dict[str, str] = Field(
        default_factory=dict, description="实体.属性 → 新值"
    )
    character_shifts: list[str] = Field(default_factory=list)
    hooks_opened: list[str] = Field(default_factory=list)
    hooks_closed: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------

_SCENE_PLAN_SYSTEM = (
    "你是总导演，负责分场。把选定剧本拆成 2–4 张场景卡。"
    "每张卡必须写清：purpose（本场必须改变什么）、entry_state（入场谁知道什么）、"
    "protagonist_objective、opposition、core_choice（主角舍弃什么选择什么）、"
    "emotion_arc（从什么感受到什么）、exit_change（出场哪件事已不可逆）。"
    "每个节拍有稳定 beat_id（b1/b2…），标明 scene_slot 与 must_show。"
    "禁止把整章塞进一场；禁止每场都公开打脸；转折前后应各有承载。"
    "首场 must_show 宜少：站稳人物处境 + 讲清金手指规则；大兑现与代价放后场。"
    "所有 beat_id 必须唯一；所有 scene_id 必须唯一。"
    "必须覆盖原剧本的关键节拍，不得遗漏转折与代价。"
    "continuity_notes 写跨场连续性：情绪、道具、信息隔离。"
    "冲突尺度匹配当场，禁止小题大做。"
)

_SCENE_WRITE_SYSTEM = SCENE_WRITE_SYSTEM

_AUDIT_SYSTEM = (
    "你是场记，一次完成三项工作。\n"
    "一、节拍核验：对每个 beat_id 判定 landed——正文是否真正呈现了该节拍的"
    "选择、回应与后果；landed=True 必须给出 evidence 原文摘录（须在正文中）。"
    "只查关键词不算落地。\n"
    "二、连续性：continuity_issues 列出本场与入场状态/上场结尾的矛盾"
    "（人物不知的事不能知道、已关闭的钩子不能再吊、已耗尽资源不能恢复）。"
    "hard_fails 列出跳场、改因果终点、引入弃选路线、核心节拍完全缺失。\n"
    "三、实际变化：从正文提取已发生的变化（计划中但正文未呈现的不算）。"
    "facts 写不可逆事实；state_changes 写 实体.属性→新值；"
    "character_shifts 写人物态度实际变化；hooks_opened/closed 写钩子开关。"
)

_EDIT_SYSTEM = (
    "你是剪辑师，把各场正文合并成完整章节。"
    "只做节奏调整、删重复、衔接润色；不得改写任何场次的因果终点与核心选择。"
    "保持单一主角视角。合并后应自然流畅，场与场之间不突兀。"
    "输出完整章节正文，不要场次分隔标记。"
)


# ---------------------------------------------------------------------------
# 分场计划校验：实现在 domain.scene_card.validate_scene_plan，此处保持导入名兼容。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 协议实现
# ---------------------------------------------------------------------------


async def run_protocol_x(
    provider: MeteredProvider,
    fragment: dict[str, str],
    *,
    max_revisions: int = 2,
    max_scenes: int = 4,
    fixed_packet: dict[str, Any] | None = None,
) -> SampleResult:
    """协议 X：两剧本择优 → 分场 → 逐场执笔+场记 → 组装 → 核验 → 验收。

    ``fixed_packet``：对照实验用——直接使用预生成的制作包，跳过编剧与选本。
    """
    steps: list[str] = []
    artifacts: dict[str, Any] = {
        "protocol_name": "scene_exec_then_write",
        "candidates": {},
        "rejected": {},
        "fault_taxonomy": {"supply": None, "pick": None, "render": None},
    }
    used_revision = False
    stop_reason = ""
    prose = ""
    scene_hard_fails: list[str] = []
    chapter_hard_fails: list[str] = []
    facts: list[str] = []
    facts_committed = False
    try:
        base = _fragment_payload(fragment)

        if fixed_packet is not None:
            steps.append("use_fixed_packet")
            packet = fixed_packet
            selected = ChapterScript.model_validate(packet.get("script") or {})
            choice = ScriptChoice.model_validate(packet.get("direction") or {})
            selected_id = "fixed"
            artifacts["candidates"] = {"fixed": selected.model_dump()}
            artifacts["selected_id"] = "fixed"
            artifacts["production_packet"] = packet
            artifacts["fixed_packet"] = True
        else:
            # --- 导演 BRIEF → 编剧×N → 选本（与 S 共用） ---
            from regent.novel.experiments.quality_ab import build_production_packet

            packet, packet_arts, packet_steps, packet_stop = await build_production_packet(
                provider,
                fragment,
                protocol=PROTOCOL_SCRIPT_SCENE,
            )
            steps.extend(packet_steps)
            artifacts.update(
                {
                    k: v
                    for k, v in packet_arts.items()
                    if k
                    in {
                        "candidates",
                        "rejected",
                        "assignment",
                        "assignment_goal",
                        "hive",
                        "fault_taxonomy",
                        "candidate_slots",
                        "director_choice",
                        "selected_id",
                        "production_packet",
                    }
                }
            )
            if packet is None or packet_stop:
                stop_reason = packet_stop or "reject_both_scripts"
                return _finalize_result(
                    fragment_id=fragment["id"],
                    protocol="X",
                    provider=provider,
                    prose="",
                    hard_fails=["no_viable_script"],
                    used_revision=False,
                    stop_reason=stop_reason,
                    facts=[],
                    facts_committed=False,
                    steps=steps,
                    artifacts=artifacts,
                )

            selected_id = str(artifacts.get("selected_id") or "")
            selected = ChapterScript.model_validate(
                (artifacts.get("candidates") or {})[selected_id]
            )
            choice = ScriptChoice.model_validate(artifacts.get("director_choice") or {})
            artifacts["production_packet"] = packet

        # --- 导演分场（含完整性校验） ---
        steps.append("compile_scenes")
        skeleton = compile_scene_plan(selected, choice, chapter_no=0)
        plan_issues: list[str] = ["initial"]
        plan = ChapterScenePlan(
            chapter_no=0,
            cards=skeleton.cards,
            chapter_goal=selected.end_change,
            continuity_notes=skeleton.continuity_notes,
        )
        # 最多两轮：第一轮模型分场，第二轮带问题退回
        for attempt in range(2):
            plan_resp = await provider.generate_structured(
                system_prompt=_SCENE_PLAN_SYSTEM
                + (
                    f"\n上一轮分场有问题，必须修正：{'；'.join(plan_issues[:5])}"
                    if attempt > 0
                    else ""
                ),
                user_prompt=_dump(
                    {
                        **base,
                        "selected_script": selected.model_dump(),
                        "direction": choice.model_dump(),
                        "skeleton_plan": skeleton.model_dump(),
                        "max_scenes": max_scenes,
                        "previous_issues": plan_issues if attempt > 0 else [],
                    }
                ),
                response_model=ScenePlanDraft,
            )
            plan_draft = plan_resp.output
            assert isinstance(plan_draft, ScenePlanDraft)
            # 不静默截断：超限直接记问题退回
            plan = ChapterScenePlan(
                chapter_no=0,
                cards=list(plan_draft.cards),
                chapter_goal=plan_draft.chapter_goal or selected.end_change,
                continuity_notes=plan_draft.continuity_notes,
            )
            plan_issues = validate_scene_plan(
                plan, selected, choice, max_scenes=max_scenes
            )
            steps.append(f"compile_scenes_try{attempt + 1}")
            if not plan_issues:
                break
        if plan_issues:
            # 两轮仍有覆盖/结构问题：优先退回骨架；骨架仅 must_land 映射失败时仍可执笔
            # （场记会按文本验收），禁止只因导演措辞与节拍字面不一整章空跑。
            from regent.novel.domain.scene_card import structural_plan_issues

            fallback_plan = ChapterScenePlan(
                chapter_no=0,
                cards=list(skeleton.cards),
                chapter_goal=selected.end_change,
                continuity_notes=skeleton.continuity_notes,
            )
            fallback_issues = validate_scene_plan(
                fallback_plan, selected, choice, max_scenes=max_scenes
            )
            hard_fallback = structural_plan_issues(fallback_issues)
            if hard_fallback:
                scene_hard_fails.extend(f"plan:{i}" for i in plan_issues[:5])
                scene_hard_fails.extend(f"plan_fallback:{i}" for i in hard_fallback[:3])
                stop_reason = f"plan_invalid:{';'.join((plan_issues + hard_fallback)[:4])}"
                return _finalize_result(
                    fragment_id=fragment["id"],
                    protocol="X",
                    provider=provider,
                    prose="",
                    hard_fails=scene_hard_fails,
                    used_revision=used_revision,
                    stop_reason=stop_reason,
                    facts=[],
                    facts_committed=False,
                    steps=steps,
                    artifacts=artifacts,
                )
            # 骨架覆盖成立：可继续，但把导演稿未覆盖项记为诊断，不进硬失败
            artifacts["scene_plan_issues"] = list(plan_issues)
            artifacts["plan_used_skeleton"] = True
            plan = fallback_plan
        else:
            artifacts["scene_plan_issues"] = []
        # 多场时强制首场 must 上限，防止导演把整章硬卡塞进 s1
        plan = normalize_opening_must_show(plan, first_scene_must_cap=2)
        artifacts["scene_plan"] = plan.model_dump()

        must_land = must_land_ids(plan, choice.must_land_beats)

        # --- 逐场执笔 + 场记 ---
        scene_texts: list[str] = []
        working_summary = str(fragment.get("brief", ""))[:200]
        working_state: dict[str, str] = {}
        open_hooks: list[str] = [
            str(fragment.get("hook") or "")
        ] or ["本章核心悬念未解"]

        for card in plan.cards[:max_scenes]:
            scene_id = card.scene_id
            steps.append(f"write_{scene_id}")

            target = max(500, min(1500, card.target_chars or 800))
            is_opening = str(fragment.get("slot") or "") == "chapter1_opening" and scene_id == (
                plan.cards[0].scene_id if plan.cards else "s1"
            )
            write_system = _SCENE_WRITE_SYSTEM
            if is_opening:
                write_system = (
                    _SCENE_WRITE_SYSTEM
                    + OPENING_CONTRACT
                    + SCALE_CONTRACT
                    + WEB_HOOK_CONTRACT
                )
            write_payload = {
                **base,
                "scene_card": card.model_dump(),
                "entry_summary": working_summary,
                "working_state": working_state,
                "open_hooks": open_hooks[-4:],
                "chapter_goal": plan.chapter_goal,
                "power_payoff": selected.power_payoff,
                "must_land_beats": [
                    b for b in must_land if b in {bb.beat_id for bb in card.beats}
                ],
                "immersion": IMMERSION_CONTRACT,
                "density": DENSITY_CONTRACT,
                "opening": OPENING_CONTRACT if is_opening else "",
                "scale": SCALE_CONTRACT if is_opening else "",
                "target_chars": target,
            }
            written = await provider.generate_structured(
                system_prompt=write_system,
                user_prompt=_dump(write_payload),
                response_model=SceneProseWithBeats,
            )
            scene_result = written.output
            assert isinstance(scene_result, SceneProseWithBeats)
            scene_text = scene_result.content.strip()
            if len(scene_text) < 150:
                scene_hard_fails.append(f"{scene_id}_too_short")
                scene_texts.append(scene_text)
                stop_reason = f"scene_hard_fail:{scene_id}_too_short"
                break

            # 场记（合并：节拍 + 连续性 + 实际变化）
            steps.append(f"audit_{scene_id}")
            audit = await _audit_scene(
                provider,
                card=card,
                scene_text=scene_text,
                beat_evidence=scene_result.beat_evidence,
                working_summary=working_summary,
                working_state=working_state,
                open_hooks=open_hooks[-4:],
                prior_ending=scene_texts[-1][-300:] if scene_texts else "",
            )

            # 程序门控：与生产共用 evaluate_scene_audit
            scene_must = [b.beat_id for b in card.beats if b.must_show]
            gate = evaluate_scene_audit(
                must_beat_ids=scene_must,
                verdicts=audit.beat_verdicts,
                hard_fails=audit.hard_fails,
                continuity_ok=audit.continuity_ok,
                continuity_issues=audit.continuity_issues,
                scene_text=scene_text,
                working_state=working_state if isinstance(working_state, dict) else {},
            )

            # 阻断/证据问题 → 本场局部重写最多 max_revisions 次，每次重写后重新场记
            revisions_left = max(0, int(max_revisions))
            while gate.needs_revision() and revisions_left > 0:
                used_revision = True
                revisions_left -= 1
                steps.append(f"rewrite_{scene_id}")
                fix_instruction = gate.revision_instruction()
                rewritten = await provider.generate_structured(
                    system_prompt=write_system,
                    user_prompt=_dump(
                        {
                            **write_payload,
                            "previous_draft": scene_text,
                            "revision_instruction": fix_instruction,
                            "missed_beats": gate.critical_missed,
                            "is_revision": True,
                        }
                    ),
                    response_model=SceneProseWithBeats,
                )
                scene_result2 = rewritten.output
                assert isinstance(scene_result2, SceneProseWithBeats)
                if len(scene_result2.content.strip()) < 150:
                    break
                scene_result = scene_result2
                scene_text = scene_result.content.strip()
                steps.append(f"reaudit_{scene_id}")
                audit = await _audit_scene(
                    provider,
                    card=card,
                    scene_text=scene_text,
                    beat_evidence=scene_result.beat_evidence,
                    working_summary=working_summary,
                    working_state=working_state,
                    open_hooks=open_hooks[-4:],
                    prior_ending=scene_texts[-1][-300:] if scene_texts else "",
                )
                gate = evaluate_scene_audit(
                    must_beat_ids=scene_must,
                    verdicts=audit.beat_verdicts,
                    hard_fails=audit.hard_fails,
                    continuity_ok=audit.continuity_ok,
                    continuity_issues=audit.continuity_issues,
                    scene_text=scene_text,
                    working_state=working_state if isinstance(working_state, dict) else {},
                )

            # 未解决硬问题：记入后立刻停场循环，不污染后续场状态承接
            if gate.blocking:
                scene_hard_fails.extend(gate.hard_fail_tags(scene_id))

            artifacts.setdefault("scene_audits", {})[scene_id] = {
                **audit.model_dump(),
                "gate": {
                    "blocking": gate.blocking,
                    "critical_missed": gate.critical_missed,
                    "evidence_fails": gate.evidence_fails,
                    "hard_fails": gate.hard_fails,
                    "continuity_block": gate.continuity_block,
                },
            }
            if gate.evidence_fails and not gate.blocking:
                artifacts.setdefault("soft_evidence_warns", []).extend(
                    f"{scene_id}:{e}" for e in gate.evidence_fails[:4]
                )
            scene_texts.append(scene_text)

            if gate.blocking:
                stop_reason = f"scene_hard_fail:{scene_id}"
                break

            # 状态承接：仅本场过审后写入
            working_state.update(audit.state_changes)
            working_summary = (
                f"上场结尾：{scene_text[-300:]}\n"
                f"累计状态：{json.dumps(working_state, ensure_ascii=False)[:400]}"
            )
            for h in audit.hooks_opened:
                h = str(h).strip()
                if h and h not in open_hooks:
                    open_hooks.append(h)
            closed = {str(h).strip() for h in audit.hooks_closed if h}
            if closed:
                open_hooks = [h for h in open_hooks if h not in closed]

        if stop_reason and str(stop_reason).startswith("scene_hard_fail"):
            artifacts["scene_hard_fails"] = list(scene_hard_fails)
            return _finalize_result(
                fragment_id=fragment["id"],
                protocol="X",
                provider=provider,
                prose="\n\n".join(scene_texts),
                hard_fails=scene_hard_fails,
                used_revision=used_revision,
                stop_reason=stop_reason,
                facts=[],
                facts_committed=False,
                steps=steps,
                artifacts=artifacts,
            )

        if not scene_texts:
            stop_reason = "no_scene_prose"
            return _finalize_result(
                fragment_id=fragment["id"],
                protocol="X",
                provider=provider,
                prose="",
                hard_fails=scene_hard_fails or ["no_scene_prose"],
                used_revision=used_revision,
                stop_reason=stop_reason,
                facts=[],
                facts_committed=False,
                steps=steps,
                artifacts=artifacts,
            )

        # --- 整章组装：先拼接，必要时才剪辑 ---
        assembled = assemble_chapter(
            list(zip(plan.cards[: len(scene_texts)], scene_texts))
        )
        if len(scene_texts) == 1:
            prose = scene_texts[0]
        else:
            # 按需剪辑：只有拼接后过短或场间衔接可疑时才调用
            need_edit = len(assembled.content) < MIN_PROSE_CHARS
            if need_edit:
                steps.append("edit_chapter")
                edit_resp = await provider.generate_structured(
                    system_prompt=_EDIT_SYSTEM + DENSITY_CONTRACT + IMMERSION_CONTRACT,
                    user_prompt=_dump(
                        {
                            **base,
                            "scene_texts": scene_texts,
                            "chapter_goal": plan.chapter_goal,
                            "min_chars": MIN_PROSE_CHARS,
                            "target_chars": TARGET_PROSE_CHARS,
                        }
                    ),
                    response_model=SceneProse,
                )
                edited = edit_resp.output
                assert isinstance(edited, SceneProse)
                merged = edited.content.strip()
                prose = merged if len(merged) >= 150 else assembled.content
            else:
                prose = assembled.content
                steps.append("assemble_only")
        artifacts["prose_chars"] = len(prose)

        # --- 整章核验（与场记问题合并，不覆盖） ---
        steps.append("validate")
        report = await provider.generate_structured(
            system_prompt=(
                "核验正文是否兑现选定剧本的关键节拍与代价，有无越权新增事实。"
                "文学姿态不是硬失败；突破剧本终点或引入弃选路线是硬失败。"
                f"短于{MIN_PROSE_CHARS}字记 too_short；注水记 padded。"
            ),
            user_prompt=_dump(
                {
                    "prose": prose,
                    "selected_script": selected.model_dump(),
                    "must_land_beats": choice.must_land_beats,
                    "prose_chars": len(prose),
                    "min_chars": MIN_PROSE_CHARS,
                }
            ),
            response_model=ValidateReport,
        )
        vreport = report.output
        assert isinstance(vreport, ValidateReport)
        # 关键：场记问题与章节核验问题合并，不互相覆盖
        chapter_hard_fails = list(vreport.hard_fails)
        all_hard_fails = list(scene_hard_fails) + list(chapter_hard_fails)
        if _prose_too_short(prose) and "too_short" not in all_hard_fails:
            all_hard_fails.append("too_short")
        artifacts["validation"] = vreport.model_dump()
        artifacts["scene_hard_fails"] = list(scene_hard_fails)

        length_fails = [f for f in all_hard_fails if f != "too_short"]

        # --- 导演验收 ---
        steps.append("director_accept")
        accept_resp = await provider.generate_structured(
            system_prompt=(
                "你是总导演，做整章创作验收（不是事实核验器）。"
                "判断读者是否愿意继续、能否代入主角权衡。"
                "若失败，fault 三选一：supply（候选本就没有好戏）、"
                "pick（有更好候选却选错）、render（剧本可拍但正文写坏）。"
                "通过则 fault=ok。"
            ),
            user_prompt=_dump(
                {
                    "prose": prose,
                    "selected_id": selected_id,
                    "direction": choice.model_dump(),
                    "candidates": {
                        cid: {
                            "end_change": (c or {}).get("end_change"),
                            "cost": (c or {}).get("cost"),
                            "cost_type": (c or {}).get("cost_type"),
                        }
                        for cid, c in artifacts["candidates"].items()
                    },
                    "scene_hard_fails": scene_hard_fails,
                    "validation_hard_fails": chapter_hard_fails,
                }
            ),
            response_model=ChapterAccept,
        )
        accept = accept_resp.output
        assert isinstance(accept, ChapterAccept)
        artifacts["director_accept"] = accept.model_dump()
        fault = (accept.fault or "").strip().lower()
        if fault in {"supply", "pick", "render"}:
            artifacts["fault_taxonomy"][fault] = True
        if length_fails:
            artifacts["fault_taxonomy"]["render"] = True

        if accept.accept and not length_fails and not _prose_too_short(prose):
            steps.append("extract_facts_from_prose")
            facts_resp = await provider.generate_structured(
                system_prompt=(
                    "从这段最终正文中提取已发生的不可逆事实、新开/关闭的钩子、"
                    "人物态度变化。只提取正文里读者能看见的结果；"
                    "剧本计划但正文未呈现的不算。"
                ),
                user_prompt=_dump(
                    {
                        "prose": prose,
                        "selected_script_end_change": selected.end_change,
                        "selected_script_cost": selected.cost,
                    }
                ),
                response_model=ProseFacts,
            )
            prose_facts = facts_resp.output
            assert isinstance(prose_facts, ProseFacts)
            facts = [f for f in prose_facts.facts if f and f.strip()]
            artifacts["prose_facts"] = prose_facts.model_dump()
            artifacts["hooks_opened"] = list(prose_facts.hooks_opened)
            artifacts["hooks_closed"] = list(prose_facts.hooks_closed)
            artifacts["character_shifts"] = list(prose_facts.character_shifts)
            if not facts:
                artifacts["facts_status"] = "accepted_but_no_extractable_facts"
                facts_committed = False
                stop_reason = "no_extractable_facts"
            else:
                facts_committed = True
                artifacts["facts_status"] = "committed_from_final_prose"
        else:
            artifacts["facts_status"] = "rejected_remain_draft"
            if not accept.accept and not stop_reason:
                stop_reason = f"director_reject:{fault or 'unknown'}"

    except BudgetExhausted as exc:
        stop_reason = f"budget:{exc}"
    except Exception as exc:  # noqa: BLE001
        stop_reason = f"error:{type(exc).__name__}:{exc}"

    # 最终 hard_fails = 场记未解决问题 + 章节核验问题
    final_hard_fails = list(scene_hard_fails) + list(chapter_hard_fails)
    return _finalize_result(
        fragment_id=fragment["id"],
        protocol="X",
        provider=provider,
        prose=prose,
        hard_fails=final_hard_fails,
        used_revision=used_revision,
        stop_reason=stop_reason,
        facts=facts,
        facts_committed=facts_committed,
        steps=steps,
        artifacts=artifacts,
    )


async def _audit_scene(
    provider: MeteredProvider,
    *,
    card: SceneCard,
    scene_text: str,
    beat_evidence: dict[str, str],
    working_summary: str,
    working_state: dict[str, str],
    open_hooks: list[str],
    prior_ending: str,
) -> SceneAudit:
    """一次场记调用：节拍判定 + 连续性 + 实际变化。"""
    resp = await provider.generate_structured(
        system_prompt=_AUDIT_SYSTEM,
        user_prompt=_dump(
            {
                "scene_card": card.model_dump(),
                "scene_text": scene_text,
                "beat_evidence": beat_evidence,
                "entry_summary": working_summary,
                "working_state": working_state,
                "open_hooks": open_hooks,
                "prior_scene_ending": prior_ending,
            }
        ),
        response_model=SceneAudit,
    )
    audit = resp.output
    assert isinstance(audit, SceneAudit)
    # 程序检查完整性：每个 must_show 节拍必须有 verdict
    must_ids = {b.beat_id for b in card.beats if b.must_show}
    verdict_ids = {v.beat_id for v in audit.beat_verdicts}
    for missing in sorted(must_ids - verdict_ids):
        audit.beat_verdicts.append(
            BeatVerdict(beat_id=missing, landed=False, note="场记未给出判定，按未落地处理")
        )
    return audit


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
