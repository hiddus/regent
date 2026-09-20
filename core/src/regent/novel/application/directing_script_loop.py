"""Script production loop."""

# ruff: noqa: RUF001
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.directing_calls import (
    _call,
    _call_batch,
    _command_id,
    _input_version,
    _save,
    _script_scene_command_id,
    hydrate_call_key_version,
)
from regent.novel.application.directing_contracts import (
    SceneText,
    ScriptChapterValidation,
    _SceneAudit,
    _ScenePlanDraft,
    _SceneProseWithBeats,
)
from regent.novel.application.directing_planning import (
    MAX_PLAN_REPAIRS,
)
from regent.novel.application.directing_protocol import (
    production_protocol,
)
from regent.novel.application.directing_runtime import (
    _RUNTIME,
    _ensure_validate_call_reserve,
)
from regent.novel.application.directing_types import (
    MAX_CHAPTER_CREATIVE_REPAIRS,
    PROTOCOL_SCRIPT,
    PROTOCOL_SCRIPT_SCENE,
)
from regent.novel.application.runtime import (
    RuntimeState,
)
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.errors import (
    ProductionStopped,
)
from regent.novel.domain.memory import project_payloads as _memory_view
from regent.novel.domain.prose_front_gates import (
    front_gate_hard_fails,
    front_gate_writer_hint,
)
from regent.novel.domain.repair_locate import (
    apply_repair_target_to_script_state,
    locate_fail_messages,
    select_repair_target,
    verify_repair_progress,
)
from regent.novel.domain.scene_card import (
    SCENE_WRITE_SYSTEM,
    ChapterScenePlan,
    SceneCard,
    assemble_chapter,
    compile_scene_plan,
    evaluate_scene_audit,
    structural_plan_issues,
    validate_scene_plan,
)
from regent.novel.domain.script_protocol import (
    DIRECTOR_ASSIGN_CONTRACT,
    DIRECTOR_SELECT_CONTRACT,
    SCRIPT_CANDIDATE_SLOTS,
    SCRIPT_DIVERSITY_CONTRACT,
    SCRIPT_PHASES,
    SCRIPT_SCENE_PHASES,
    WEB_NOVEL_POWER_CONTRACT,
    ChapterCreativeAccept,
    ChapterScript,
    ScriptAssignmentBoard,
    ScriptChoice,
    assemble_production_packet,
    assert_rejected_isolated,
    empty_script_state,
    normalize_assignment_board,
    route_script_hive,
    task_for_slot,
)
from regent.novel.domain.states import SceneArtifact, SceneRunState
from regent.novel.domain.world_bible import (
    resolve_prose_style,
)
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel


def _script_writer_system(prose_style: dict[str, Any] | None = None, *, chapter_no: int = 1) -> str:
    base = (
        "你是小说执笔者。按选定剧本与导演执导要求，完成整章文学演绎。"
        "统合动作、对白与心理；锁定单一主角有限视角。"
        "必须让金手指在正文中被读者「看见兑现」，不能只靠说明。"
        "不得改写剧本因果终点与 must_land_beats；不得引入弃选剧本情节。"
        "正文目标约1800至2500字。" + WEB_NOVEL_POWER_CONTRACT
    )
    hint = front_gate_writer_hint(prose_style=prose_style, chapter_no=chapter_no)
    return base + ((" " + hint) if hint else "")


def _script_system() -> str:
    return (
        "你是网文章节编剧，不是导演，也不是小说执笔者。"
        "导演只给了宽松选题（writer_task）：请自行发明戏核、代价与转折；不要把选题扩写成死步骤。"
        "产出一份紧凑可演的章节剧本：写清行动、回应、关键选择、转折原因、代价、结尾变化，"
        "并带少量关键对白与主角权衡。"
        "必须填写 power_mechanism、power_limits、cost_type、power_payoff；"
        "机制与代价由本候选发明，选中后成为跨章正史。"
        "禁止只写提纲口号；禁止写成完整小说正文；禁止预写镜头表。"
        "本候选是草稿：不得假设已被选中或已进入正式世界；不得要求登记正式角色表。"
        + WEB_NOVEL_POWER_CONTRACT
        + SCRIPT_DIVERSITY_CONTRACT
    )


def _script_brief_from_packet(
    production: dict[str, Any],
    packet: dict[str, Any],
    *,
    prose_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    script = packet.get("script") or {}
    cast_names = list(production.get("cast") or {})
    lead = cast_names[0] if cast_names else "主角"
    ps = prose_style if isinstance(prose_style, dict) else {}
    # 视角跟行文契约，禁止默默用 cast[0]（穿越者名常排在宿主前）
    viewpoint = str(ps.get("viewpoint") or "").strip() or lead
    distance = str(ps.get("narrative_distance") or "").strip() or "近"
    return {
        "purpose": str(script.get("end_change") or "script_chapter"),
        "setting": str(script.get("start_state") or ""),
        "conflict": str(script.get("opposition") or ""),
        "exit_condition": str(script.get("end_change") or ""),
        "actors": [
            {
                "persona": name,
                "objective": str(script.get("protagonist_want") or ""),
                "instruction": "按选定剧本演绎",
                "role": "",
            }
            for name in cast_names
        ]
        or [
            {
                "persona": lead,
                "objective": str(script.get("protagonist_want") or ""),
                "instruction": "按选定剧本演绎",
                "role": "",
            }
        ],
        "narrative": {
            "viewpoint": viewpoint,
            "distance": distance,
            "style": str(ps.get("tone") or ""),
            "reader_effect": str(script.get("reading_question") or ""),
            "disclosure_rule": "不泄露未知秘密",
        },
    }


def _ensure_script_take(production: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    if production.get("takes"):
        return production["takes"][-1]
    style = production.get("prose_style") if isinstance(production.get("prose_style"), dict) else {}
    take = {
        "scene_index": 0,
        "take_no": 1,
        "brief": _script_brief_from_packet(production, packet, prose_style=style),
        "state_before": deepcopy(production.get("working_state") or {}),
        "turn": 0,
        "performances": [],
        "round_actions": [],
        "events": [],
        "revisions": 0,
        "prose_versions": [],
        "status": "DRAFT",
        "scene_state": SceneRunState.RENDERING.value,
        "artifact": "",
    }
    production["takes"] = [take]
    return take


def _audit_cont_softable(gate: Any, *, working_state: dict[str, Any] | None = None) -> bool:
    """仅显式 soft-cont / 已核空入场发现误报可 soft；禁止关键词猜测真实矛盾。"""
    from regent.novel.domain.scene_card import is_empty_state_discovery_false_positive

    if gate.critical_missed:
        return False
    hard = [str(h) for h in (gate.hard_fails or []) if str(h).strip()]
    cont_issues = [str(x) for x in (getattr(gate, "continuity_issues", None) or [])]
    blob = hard + cont_issues
    if not blob:
        return False
    state = working_state if isinstance(working_state, dict) else None
    for h in blob:
        if h.startswith("[soft-cont:"):
            continue
        # 必须传入实际 working_state：非空已知状态下不得 soft 知识矛盾
        if is_empty_state_discovery_false_positive(h, working_state=state):
            continue
        return False
    return True


def _script_cards(sp: dict[str, Any]) -> list[Any]:
    plan = sp.get("scene_plan") or {}
    return list(plan.get("cards") or [])


def _verify_model_location(
    *,
    data: dict[str, Any],
    cards: list[Any],
    texts: list[str],
    full_chapter: str,
) -> tuple[str, ...] | None:
    """核验模型定位：合法 scene_id + 引文接地 + hash 一致；否则返回 None。

    程序生成/核对正文 hash，逐个校验引用与场景归属。
    无法证明对应待修问题的定位不得改变目标。
    """
    from regent.novel.domain.repair_locate import content_hash as _chash
    from regent.novel.domain.scene_card import evidence_grounded

    sids = tuple(str(x) for x in (data.get("scene_ids") or []) if str(x))
    if not sids:
        return None
    known = {
        str((c.get("scene_id") if isinstance(c, dict) else getattr(c, "scene_id", "")) or "")
        for c in cards
    }
    known.discard("")
    if any(sid not in known for sid in sids):
        return None
    # content_hash 若给出，必须与当前完整正文一致
    model_hash = str(data.get("content_hash") or "").strip()
    if model_hash and model_hash != _chash(full_chapter):
        return None
    quotes = tuple(str(q) for q in (data.get("evidence_quotes") or []) if str(q))
    if not quotes:
        # 无引文：仅允许 code 为 beat/miss 类（用卡片映射验证），其余拒绝
        code = str(data.get("code") or "")
        if not any(k in code for k in ("beat", "miss", "missing")):
            return None
        # beat 类：message 中的 beat_id 必须落在所指场景卡上
        msg = str(data.get("message") or "")
        for sid in sids:
            card = next(
                (
                    c
                    for c in cards
                    if str(
                        (c.get("scene_id") if isinstance(c, dict) else getattr(c, "scene_id", ""))
                    )
                    == sid
                ),
                None,
            )
            if card is None:
                return None
            beats = (
                card.get("beats")
                if isinstance(card, dict)
                else getattr(card, "beats", []) or []
            )
            beat_ids = {
                str((b.get("beat_id") if isinstance(b, dict) else getattr(b, "beat_id", "")))
                for b in beats
            }
            if beat_ids and not any(bid and bid in msg for bid in beat_ids):
                return None
        return sids
    # 有引文：每条引文必须出现在所指场景正文中
    for sid in sids:
        idx = next(
            (
                i
                for i, c in enumerate(cards)
                if str(
                    (c.get("scene_id") if isinstance(c, dict) else getattr(c, "scene_id", ""))
                )
                == sid
            ),
            None,
        )
        if idx is None or idx >= len(texts):
            return None
        scene_text = texts[idx]
        if not any(evidence_grounded(q, scene_text) for q in quotes):
            return None
    return sids


def _begin_located_scene_repair(
    production: dict[str, Any],
    sp: dict[str, Any],
    *,
    fails: list[str],
    instruction_prefix: str,
    take: dict[str, Any] | None = None,
    model_located: list[Any] | None = None,
) -> None:
    """按定位结果回退到目标场；无法定位则停机。禁止默认修末场。

    模型定位只作核验后的补齐：合法 ID + 引文接地 + hash 一致才能改写目标。
    """
    from regent.novel.domain.repair_locate import (
        LocatedIssue,
        apply_repair_target_to_script_state,
        locate_fail_messages,
        select_repair_target,
    )

    cards = _script_cards(sp)
    texts = list(sp.get("scene_texts") or [])
    full_chapter = "\n\n".join(t for t in texts if t.strip())
    located = locate_fail_messages(
        fails, scene_texts=texts, cards=cards, chapter_content=full_chapter
    )
    # 确定性已定位的问题身份（code+message 前缀），模型只补齐缺口
    deterministic_keys = {
        (i.code, str(i.message or "")[:60]) for i in located if i.scene_ids
    }
    deterministic_codes_located = {i.code for i in located if i.scene_ids}
    # 模型定位：逐条核验，只在确定性未覆盖时补齐
    for raw in model_located or []:
        try:
            data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        except Exception:
            continue
        verified_sids = _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full_chapter
        )
        if not verified_sids:
            continue
        code = str(data.get("code") or "model")
        msg = str(data.get("message") or data.get("issue_id") or code)
        # 同一问题身份已被确定性定位 → 不重复追加、不覆盖
        if (code, msg[:60]) in deterministic_keys:
            continue
        if code in deterministic_codes_located and any(
            i.code == code and i.scene_ids for i in located
        ):
            # 同 code 已有确定性定位：模型猜测不得改变目标
            continue
        quotes = tuple(str(q) for q in (data.get("evidence_quotes") or []) if str(q))
        located.append(
            LocatedIssue(
                issue_id=str(data.get("issue_id") or f"{code}#model"),
                code=code,
                severity="hard" if str(data.get("severity") or "hard") == "hard" else "soft",
                scene_ids=verified_sids,
                evidence_quotes=quotes,
                content_hash=str(data.get("content_hash") or ""),
                expected_action=str(data.get("expected_action") or "rewrite_scene"),
                verify_rule=str(data.get("verify_rule") or ""),
                message=msg,
            )
        )
    target = select_repair_target(
        located,
        cards=cards,
        scene_texts=texts,
        instruction_prefix=instruction_prefix,
    )
    sp["located_issues"] = [i.as_dict() for i in located]
    if take is not None:
        validation = dict(take.get("validation") or {})
        validation["located_issues"] = sp["located_issues"]
        take["validation"] = validation
    if not target.locatable:
        raise ProductionStopped(target.instruction)
    apply_repair_target_to_script_state(sp, target)
    # 截断不变量：目标场必须是列表末项，WRITE/AUDIT 用显式 idx 并可断言
    texts_after = list(sp.get("scene_texts") or [])
    idx = int(sp.get("scene_index") or 0)
    if texts_after and idx != len(texts_after) - 1:
        raise ProductionStopped(
            f"定位后 scene_index={idx} 与 scene_texts 长度={len(texts_after)} 不一致"
        )
    production["phase"] = "WRITE_SCENE"


def _bump_creative_repair(production: dict[str, Any], sp: dict[str, Any]) -> None:
    repairs = int(sp.get("creative_repairs") or 0) + 1
    sp["creative_repairs"] = repairs
    production["chapter_repairs"] = repairs
    if repairs > MAX_CHAPTER_CREATIVE_REPAIRS:
        raise ProductionStopped("章节创作返工次数已达上限，保留草稿")


async def _produce_script_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
) -> bool:
    """剧本择优协议阶段机：一 tick 推进一阶段。"""
    phase = str(production.get("phase") or "")
    if phase == "DONE":
        return True
    proto = production_protocol(production)
    allowed_phases = SCRIPT_SCENE_PHASES if proto == PROTOCOL_SCRIPT_SCENE else SCRIPT_PHASES
    if phase not in allowed_phases:
        raise ProductionStopped(f"未知剧本协议阶段: {phase}")
    sp = production.setdefault("script_protocol", empty_script_state())
    hydrate_call_key_version(production, run)
    # 行文契约前置进 production，执笔/brief/VALIDATE 共用，不留给责编补洞。
    style = resolve_prose_style(run.generation_context)
    if style:
        production["prose_style"] = style
    prefix = f"script:{phase}"
    memory_payloads = list(run.generation_context.get("memory", []) or [])

    async def call[T: BaseModel](
        schema: type[T], system: str, payload: dict[str, Any], *, repair_no: int = 0
    ) -> T:
        # 剧本臂始终用场景感知标识：审校回退后 takes 非空，若仍走 take 维度
        # _command_id，会忽略 scene_id/srep，第二场与重写撞幂等键。
        command_id = _script_scene_command_id(run, production, sp, phase)
        if repair_no:
            command_id = f"{command_id}:r{repair_no}"
        return await _call(
            session,
            provider,
            work,
            run,
            production,
            schema,
            system,
            payload,
            prefix,
            command_id,
        )

    # 请求分层：固定规则 → 作品资料 → 章节材料 → 本次任务。同层稳定序列化，
    # 不用全局字母序代替内容分层。
    _ctx_order = (
        "work_rules",
        "world_rules",
        "canon",
        "story_ledger_block",
        "open_hooks",
        "recent_chapters",
        "target_node",
        "chapter_brief",
        "verified_facts",
        "actual_state",
        "user_guidance",
        "architecture_version",
        "chapter_no",
    )
    raw_ctx = {k: v for k, v in run.generation_context.items() if k not in ("production", "memory")}
    ordered_ctx: dict[str, Any] = {}
    for key in _ctx_order:
        if key in raw_ctx:
            ordered_ctx[key] = raw_ctx[key]
    for key in sorted(raw_ctx):
        if key not in ordered_ctx:
            ordered_ctx[key] = raw_ctx[key]
    base_payload = {
        "context": ordered_ctx,
        "cast": production.get("cast") or {},
        "user_guidance": run.user_guidance or {},
        "director_memory": _memory_view(memory_payloads, "director"),
        # canon 只放在 context 分层里，避免外层再传一份重复计费
    }

    if phase == "BRIEF":
        # 导演先布置简洁宽松选题；甲乙丙丁只是编号，不绑定写死策略轴。
        assign_payload: dict[str, Any] = {
            **base_payload,
            "candidate_slots": list(SCRIPT_CANDIDATE_SLOTS),
            "assign_note": (
                "给四个槽位各写一两句宽松选题；四条戏剧方向必须可区分；"
                "至少一条逼出不可两全的张力；禁止写死情节步骤、代价类型、打脸清单。"
            ),
        }
        if sp.get("last_reject_reason"):
            assign_payload["previous_reject_reason"] = sp["last_reject_reason"]
            assign_payload["previous_reject_weakness"] = sp.get("last_reject_weakness") or ""
            assign_payload["reassign_note"] = (
                "上一轮候选被退回：请换选题方向，避开诊断中的套路与假痛点。"
            )
        board = await call(
            ScriptAssignmentBoard,
            DIRECTOR_ASSIGN_CONTRACT,
            assign_payload,
        )
        assignment = normalize_assignment_board(board)
        sp["assignment"] = assignment
        sp["assignment_goal"] = str(board.goal_restated or "").strip()
        production["decisions"].append(
            {
                "phase": phase,
                "goal_restated": sp["assignment_goal"],
                "tasks": [{"slot": k, "task": v} for k, v in assignment.items()],
            }
        )
        production["phase"] = "WRITE_SCRIPTS"
    elif phase == "WRITE_SCRIPTS":
        # 编剧 Hive：甲乙丙丁互不读稿，同 tick 并发；隔离失败则退回串行。
        assignment = sp.get("assignment") or {}
        if not assignment:
            raise ProductionStopped("编剧 Hive 缺少导演选题板，请先 BRIEF")
        slot_payloads: dict[str, dict[str, Any]] = {}
        for slot in SCRIPT_CANDIDATE_SLOTS:
            item: dict[str, Any] = {
                **base_payload,
                "candidate_slot": slot,
                "writer_task": task_for_slot(assignment, slot),
                "assignment_goal": sp.get("assignment_goal") or "",
                "note": "本请求只写这一条选题；自行发明戏核与代价；不得读取其他候选。",
            }
            if sp.get("last_reject_reason"):
                item["previous_reject_reason"] = sp["last_reject_reason"]
                item["previous_reject_weakness"] = sp.get("last_reject_weakness") or ""
                item["regenerate_note"] = (
                    "上一轮候选均被退回，必须避开诊断中的套路与弱点，给出真正不同的因果路线。"
                )
            slot_payloads[slot] = item
        hive = route_script_hive(slot_payloads)
        sp["hive"] = hive
        production["decisions"].append({"phase": phase, "hive": hive})
        ordered = [(slot, slot_payloads[slot]) for slot in SCRIPT_CANDIDATE_SLOTS]
        drafts: list[ChapterScript]
        if hive.get("enabled"):
            drafts = await _call_batch(
                session,
                provider,
                work,
                run,
                production,
                ChapterScript,
                _script_system(),
                ordered,
                prefix,
                _script_scene_command_id(run, production, sp, phase),
            )
        else:
            drafts = []
            base_cid = _script_scene_command_id(run, production, sp, phase)
            for slot, payload in ordered:
                drafts.append(
                    await _call(
                        session,
                        provider,
                        work,
                        run,
                        production,
                        ChapterScript,
                        _script_system(),
                        payload,
                        prefix,
                        f"{base_cid}:{slot}",
                    )
                )
        candidates = sp.setdefault("candidates", {})
        for slot, draft in zip(SCRIPT_CANDIDATE_SLOTS, drafts, strict=False):
            candidates[slot] = draft.model_dump(mode="json")
        production["phase"] = "SELECT"
    elif phase == "SELECT":
        choice = await call(
            ScriptChoice,
            DIRECTOR_SELECT_CONTRACT,
            {
                **base_payload,
                "candidates": sp.get("candidates") or {},
                "assignment": sp.get("assignment") or {},
                "assignment_goal": sp.get("assignment_goal") or "",
                "diversity_contract": SCRIPT_DIVERSITY_CONTRACT,
                "candidate_slots": list(SCRIPT_CANDIDATE_SLOTS),
            },
        )
        selected_id = str(choice.selected_id).strip().lower()
        sp["choice"] = choice.model_dump(mode="json")
        production["decisions"].append({"phase": phase, **choice.model_dump(mode="json")})
        if selected_id not in SCRIPT_CANDIDATE_SLOTS:
            sp.setdefault("fault_taxonomy", {})["supply"] = True
            sp["rejected"] = dict(sp.get("candidates") or {})
            _bump_creative_repair(production, sp)
            # 保留失败诊断；退回 BRIEF 重派选题，而不是沿用写死轴重生。
            sp["last_reject_reason"] = choice.reason or "候选同套路或均不可用"
            sp["last_reject_weakness"] = choice.weakness or ""
            sp["candidates"] = {}
            sp["selected_id"] = ""
            sp["choice"] = None
            sp["production_packet"] = None
            sp["assignment"] = {}
            sp["assignment_goal"] = ""
            production["phase"] = "BRIEF"
        else:
            sp["selected_id"] = selected_id
            sp["rejected"] = {
                sid: data
                for sid, data in (sp.get("candidates") or {}).items()
                if sid != selected_id
            }
            production["phase"] = "ASSEMBLE"
    elif phase == "ASSEMBLE":
        selected_id = str(sp.get("selected_id") or "")
        choice_data = sp.get("choice") or {}
        selected_data = (sp.get("candidates") or {}).get(selected_id)
        if not selected_data or not choice_data:
            raise ProductionStopped("装配缺少选定剧本或择优结果")
        selected = ChapterScript.model_validate(selected_data)
        choice = ScriptChoice.model_validate(choice_data)
        packet = assemble_production_packet(
            selected=selected,
            choice=choice,
            meta={
                "work_id": str(work.id),
                "chapter_no": int(run.chapter_no),
                "protocol": (
                    PROTOCOL_SCRIPT_SCENE if proto == PROTOCOL_SCRIPT_SCENE else PROTOCOL_SCRIPT
                ),
            },
        )
        assert_rejected_isolated(
            packet,
            sp.get("rejected") or {},
            selected=selected,
        )
        sp["production_packet"] = packet
        # script_scene 走逐场演绎；script 走整章执笔
        if proto == PROTOCOL_SCRIPT_SCENE:
            production["phase"] = "COMPILE_SCENES"
        else:
            production["phase"] = "WRITE_CHAPTER"
    elif phase == "COMPILE_SCENES":
        packet = sp.get("production_packet")
        if not packet:
            raise ProductionStopped("分场缺少制作包")
        selected = ChapterScript.model_validate(packet.get("script") or {})
        choice = ScriptChoice.model_validate(packet.get("direction") or {})
        skeleton = compile_scene_plan(selected, choice, chapter_no=int(run.chapter_no))
        compile_repair: list[str] = []
        plan_draft = None
        plan_issues: list[str] = []
        for repair_no in range(MAX_PLAN_REPAIRS + 1):
            compile_payload: dict[str, Any] = {
                **base_payload,
                "selected_script": selected.model_dump(),
                "direction": choice.model_dump(),
                "skeleton_plan": skeleton.model_dump(),
                "max_scenes": 4,
                "coverage_note": (
                    "节拍正文须嵌入选定剧本 turning_point / cost / end_change 的关键短语，"
                    "以及 must_land_beats 的可读效果，便于覆盖校验；可改写但勿整段蒸发。"
                ),
            }
            if compile_repair:
                compile_payload["repair_instructions"] = compile_repair
            plan_draft = await call(
                _ScenePlanDraft,
                "你是总导演，负责分场。把选定剧本拆成 2–4 张场景卡。"
                "每张卡写清 purpose/entry_state/protagonist_objective/opposition/"
                "core_choice/emotion_arc/exit_change。每个节拍有唯一 beat_id。"
                "必须覆盖原剧本的转折与代价。所有 scene_id/beat_id 必须唯一。"
                "节拍文字要能让人看出转折、代价与章末变化，不要只写气氛。",
                compile_payload,
                repair_no=repair_no,
            )
            if len(plan_draft.cards) > 4:
                raise ProductionStopped(f"分场超限: {len(plan_draft.cards)}>4")
            scene_ids = [c.scene_id for c in plan_draft.cards]
            if len(scene_ids) != len(set(scene_ids)):
                raise ProductionStopped("分场 scene_id 重复")
            all_beat_ids = [b.beat_id for c in plan_draft.cards for b in c.beats]
            if len(all_beat_ids) != len(set(all_beat_ids)):
                raise ProductionStopped("分场 beat_id 重复")
            plan = ChapterScenePlan(
                chapter_no=int(run.chapter_no),
                cards=list(plan_draft.cards),
                chapter_goal=plan_draft.chapter_goal or selected.end_change,
                continuity_notes=plan_draft.continuity_notes,
            )
            plan_issues = validate_scene_plan(plan, selected, choice, max_scenes=4)
            hard = structural_plan_issues(plan_issues)
            if not hard:
                break
            compile_repair = [
                "上一版分场未通过：" + "；".join(hard[:6]) + "。",
                "请保留唯一 scene_id/beat_id，补全空场，并让节拍覆盖转折与代价。",
            ]
        else:
            hard = structural_plan_issues(plan_issues)
            if hard:
                raise ProductionStopped("分场覆盖校验失败：" + "；".join(hard[:4]))
        soft = [i for i in plan_issues if i not in structural_plan_issues(plan_issues)]
        if soft:
            sp["compile_soft_issues"] = soft
        assert plan_draft is not None
        sp["scene_plan"] = {
            "cards": [c.model_dump() for c in plan_draft.cards],
            "chapter_goal": plan_draft.chapter_goal or selected.end_change,
            "continuity_notes": plan_draft.continuity_notes,
        }
        sp["scene_index"] = 0
        sp["scene_texts"] = []
        # 从已确认状态初始化，不另起空字典；全章通过后提交同一份。
        confirmed = deepcopy(
            production.get("working_state") or run.generation_context.get("actual_state") or {}
        )
        sp["working_state"] = confirmed
        sp["scene_state_trail"] = [deepcopy(confirmed)]
        sp["working_summary"] = ""
        sp["scene_entry_summaries"] = [""]
        production["phase"] = "WRITE_SCENE"
    elif phase == "WRITE_SCENE":
        packet = sp.get("production_packet")
        plan = sp.get("scene_plan") or {}
        cards = plan.get("cards") or []
        idx = int(sp.get("scene_index") or 0)
        if idx >= len(cards):
            production["phase"] = "ASSEMBLE_CHAPTER"
            _save(run, production)
            return production["phase"] == "DONE"
        card = SceneCard.model_validate(cards[idx])
        _ensure_validate_call_reserve(production, action="RENDER_SCENE")
        target = max(400, min(1500, card.target_chars or 700))
        revision_instruction = str(sp.get("scene_revision_instruction") or "").strip()
        prior_draft = ""
        if revision_instruction and sp.get("scene_texts"):
            texts = list(sp.get("scene_texts") or [])
            # 修订身份：卡、旧稿必须同 scene_index；禁止隐含 [-1] 不变量。
            if idx >= len(texts):
                raise ProductionStopped(
                    f"修订目标场 index={idx} 与 scene_texts 长度={len(texts)} 不一致"
                )
            ticket = sp.get("pending_repair_ticket") or {}
            ticket_sid = str(ticket.get("scene_id") or "")
            if ticket_sid and ticket_sid != str(card.scene_id):
                raise ProductionStopped(
                    f"修订票据 scene_id={ticket_sid} 与当前卡 {card.scene_id} 不一致"
                )
            if int(ticket.get("scene_index", idx)) != idx:
                raise ProductionStopped(
                    f"修订票据 scene_index 与当前 index={idx} 不一致"
                )
            prior_draft = texts[idx]
        # 入口摘要快照：WRITE 前钉住本场 entry_summary，回退时可原样恢复
        entries = list(sp.get("scene_entry_summaries") or [])
        while len(entries) <= idx:
            entries.append("")
        if not revision_instruction:
            entries[idx] = str(sp.get("working_summary") or "")
        elif not str(entries[idx] or "").strip():
            entries[idx] = str(sp.get("working_summary") or "")
        sp["scene_entry_summaries"] = entries
        entry_summary = str(entries[idx] or "")
        # system 保持稳定：目标字数与修订指令放 payload，避免动态前缀打断缓存
        # 行文契约必须进 system：否则分场也会写成穿越者第一人称壳。
        style = production.get("prose_style") or resolve_prose_style(run.generation_context)
        style_hint = front_gate_writer_hint(
            prose_style=style if isinstance(style, dict) else {},
            chapter_no=int(run.chapter_no),
            cast=production.get("cast") or {},
            world_bible=(
                run.generation_context.get("world_bible")
                if isinstance(run.generation_context.get("world_bible"), dict)
                else None
            ),
            reader_contract=(
                run.generation_context.get("reader_contract")
                if isinstance(run.generation_context.get("reader_contract"), dict)
                else None
            ),
        )
        writer_system = SCENE_WRITE_SYSTEM + ((" " + style_hint) if style_hint else "")
        # 执笔只带叙述者记忆与本场材料；不重复塞 director_memory / 外层 canon
        write_payload: dict[str, Any] = {
            "context": ordered_ctx,
            "cast": production.get("cast") or {},
            "user_guidance": run.user_guidance or {},
            "scene_card": card.model_dump(),
            "entry_summary": entry_summary,
            "working_state": sp.get("working_state") or {},
            "chapter_goal": plan.get("chapter_goal") or "",
            "narrator_memory": _memory_view(memory_payloads, "narrator"),
            "target_chars": target,
        }
        if isinstance(style, dict) and style:
            write_payload["prose_style"] = style
        if prior_draft and revision_instruction:
            write_payload["previous_draft"] = prior_draft
            write_payload["revision_instruction"] = revision_instruction
            write_payload["is_revision"] = True
            ticket = sp.get("pending_repair_ticket") or {}
            if ticket:
                write_payload["repair_ticket"] = {
                    "scene_id": ticket.get("scene_id"),
                    "must_remove_quotes": list(ticket.get("must_remove_quotes") or [])[:4],
                    "evidence_quotes": list(ticket.get("evidence_quotes") or [])[:4],
                    "forbidden_claims": list(ticket.get("forbidden_claims") or [])[:4],
                    "required_facts": list(ticket.get("required_facts") or [])[:4],
                    "expected_actions": list(ticket.get("expected_actions") or [])[:4],
                    "verify_rules": list(ticket.get("verify_rules") or [])[:4],
                    "issue_ids": list(ticket.get("issue_ids") or [])[:6],
                }
        written = await call(
            _SceneProseWithBeats,
            writer_system,
            write_payload,
        )
        scene_text = written.content.strip()
        if prior_draft and revision_instruction and sp.get("scene_texts"):
            sp["scene_texts"][idx] = scene_text
        else:
            sp.setdefault("scene_texts", []).append(scene_text)
        sp["pending_scene_beat_evidence"] = written.beat_evidence
        sp["scene_revision_instruction"] = ""
        production["phase"] = "AUDIT_SCENE"
    elif phase == "AUDIT_SCENE":
        plan = sp.get("scene_plan") or {}
        cards = plan.get("cards") or []
        idx = int(sp.get("scene_index") or 0)
        if idx >= len(cards) or not sp.get("scene_texts"):
            production["phase"] = "ASSEMBLE_CHAPTER"
            _save(run, production)
            return production["phase"] == "DONE"
        card = SceneCard.model_validate(cards[idx])
        texts = list(sp.get("scene_texts") or [])
        if idx >= len(texts):
            raise ProductionStopped(
                f"AUDIT目标场 index={idx} 与 scene_texts 长度={len(texts)} 不一致"
            )
        scene_text = texts[idx]
        _audit_entries = list(sp.get("scene_entry_summaries") or [])
        _audit_entry = (
            str(_audit_entries[idx])
            if idx < len(_audit_entries) and str(_audit_entries[idx] or "").strip()
            else str(sp.get("working_summary") or "")
        )
        audit = await call(
            _SceneAudit,
            "你是场记，一次完成：节拍核验（landed 必须有 evidence 原文摘录）、"
            "连续性（与入场状态/上场结尾矛盾）、实际变化（计划中但正文未呈现的不算）。"
            "hard_fails 列出跳场、改因果终点、核心节拍完全缺失；每条尽量带摘录「…」。"
            "仅当问题是表述衔接、空状态误报或可软化的空间省略时，"
            "用前缀 [soft-cont:…] 写入 continuity_issues；真实事实矛盾不得 soft。"
            "注意：空入场状态只表示尚无已确认事实，不要把本场节拍要求的「发现/取出」"
            "写成 continuity 硬伤；真正矛盾须对照非空 working_state 或上场结尾。",
            {
                "scene_card": card.model_dump(),
                "scene_text": scene_text,
                "beat_evidence": sp.get("pending_scene_beat_evidence") or {},
                "entry_summary": _audit_entry,
                "working_state": sp.get("working_state") or {},
                "prior_scene_ending": (
                    texts[idx - 1][-300:] if idx > 0 and idx - 1 < len(texts) else ""
                ),
            },
        )
        # 程序门控：补全 must 判定 → 证据接地 → 连续性/硬失败（与实验共用）
        must_ids = {b.beat_id for b in card.beats if b.must_show}
        _prior_ending = (
            texts[idx - 1][-300:] if idx > 0 and idx - 1 < len(texts) else ""
        )
        gate = evaluate_scene_audit(
            must_beat_ids=must_ids,
            verdicts=audit.beat_verdicts,
            hard_fails=audit.hard_fails,
            continuity_ok=audit.continuity_ok,
            continuity_issues=audit.continuity_issues,
            scene_text=scene_text,
            working_state=sp.get("working_state")
            if isinstance(sp.get("working_state"), dict)
            else {},
            entry_summary=_audit_entry,
            prior_scene_ending=_prior_ending,
        )
        sp.setdefault("scene_audits", {})[card.scene_id] = audit.model_dump()

        scene_repair_key = f"scene_repairs:{card.scene_id}"
        scene_repairs = int(sp.get(scene_repair_key) or 0)
        # 审校回退补拍 / 创作已过：最多再写 1 次，随后 soft 连续类，避免 WRITE↔AUDIT 空烧。
        post_review = bool(sp.get("post_review_rewrite"))
        creative_done = bool((sp.get("creative_accept") or {}).get("accept"))
        MAX_SCENE_REPAIRS = 1 if (post_review or creative_done) else 2
        if gate.blocking and scene_repairs < MAX_SCENE_REPAIRS:
            sp[scene_repair_key] = scene_repairs + 1
            sp["scene_revision_instruction"] = gate.revision_instruction()
            # 不推进 scene_index，重写本场
            production["phase"] = "WRITE_SCENE"
            _save(run, production)
            return False

        # 修满仍阻断：缺拍/改因果硬停；仅跳场/空间连续记 soft 放行。
        # 不再对 post_review/creative_done 做「非 critical 全放行」——会把事实错带进成片。
        if gate.blocking:
            cont_softable = (not gate.critical_missed) and _audit_cont_softable(
                gate,
                working_state=sp.get("working_state")
                if isinstance(sp.get("working_state"), dict)
                else {},
            )
            if cont_softable:
                tags = gate.hard_fail_tags(card.scene_id)
                sp.setdefault("audit_soft_cont", []).extend(tags)
                sp.setdefault("scene_soft_fails", []).extend(tags)
                if post_review:
                    sp["post_review_rewrite"] = False
            else:
                sp.setdefault("scene_hard_fails", []).extend(gate.hard_fail_tags(card.scene_id))
                lingering = list(sp.get("scene_hard_fails") or [])
                _save(run, production)
                raise ProductionStopped(
                    "场记硬问题未清（本场修满），拒绝继续拍后续场：" + "；".join(lingering[-4:])
                )
        # 状态承接（只在本场定稿后写入）
        next_state = deepcopy(sp.get("working_state") or {})
        next_state.update(audit.state_changes)
        sp["working_state"] = next_state
        # trail[0]=入场状态；接受第 i 场后写入 trail[i+1]
        trail = sp.setdefault("scene_state_trail", [])
        if not trail:
            trail.append(deepcopy(next_state))
        while len(trail) <= idx:
            trail.append(deepcopy(next_state))
        if len(trail) == idx + 1:
            trail.append(deepcopy(next_state))
        else:
            trail[idx + 1] = deepcopy(next_state)
        sp["working_summary"] = (
            f"上场结尾：{scene_text[-300:]}\n"
            f"累计状态：{json.dumps(sp.get('working_state') or {}, ensure_ascii=False)[:400]}"
        )
        # 下一场的入口摘要快照：与 working_summary 同步，回退时可恢复
        entries = list(sp.get("scene_entry_summaries") or [])
        while len(entries) <= idx + 1:
            entries.append("")
        entries[idx + 1] = sp["working_summary"]
        sp["scene_entry_summaries"] = entries
        sp["scene_index"] = idx + 1
        sp["pending_scene_beat_evidence"] = {}
        sp["scene_revision_instruction"] = ""
        if sp["scene_index"] >= len(cards):
            production["phase"] = "ASSEMBLE_CHAPTER"
        else:
            production["phase"] = "WRITE_SCENE"
    elif phase == "ASSEMBLE_CHAPTER":
        scene_texts = list(sp.get("scene_texts") or [])
        if not scene_texts:
            raise ProductionStopped("组装缺少场景正文")
        plan = sp.get("scene_plan") or {}
        cards = [SceneCard.model_validate(c) for c in (plan.get("cards") or [])]
        assembled = assemble_chapter(
            list(zip(cards[: len(scene_texts)], scene_texts, strict=False))
        )
        take = _ensure_script_take(production, sp.get("production_packet") or {})
        take["content"] = assembled.content
        take["prose_versions"] = [*list(take.get("prose_versions") or []), assembled.content]
        take["scene_state"] = SceneRunState.DIRECTOR_VIEW.value
        take["artifact"] = SceneArtifact.PROSE.value
        # 组装时同步核验后的临时状态，避免 production/sp 两套 working_state 分叉
        if sp.get("working_state") is not None:
            production["working_state"] = deepcopy(sp["working_state"])
        production["phase"] = "VALIDATE"
    elif phase == "WRITE_CHAPTER":
        packet = sp.get("production_packet")
        if not packet:
            raise ProductionStopped("执笔缺少制作包")
        _ensure_validate_call_reserve(production, action="RENDER_SCENE")
        # 返工必须带旧稿与意见：只重新抽样不等于有针对性地修改。
        existing_take = (production.get("takes") or [None])[-1]
        prior_draft = ""
        revision_instruction = ""
        if existing_take:
            prior_draft = str(existing_take.get("content") or "").strip()
            revision_instruction = str(existing_take.get("revision_instruction") or "").strip()
        writer_payload: dict[str, Any] = {
            **base_payload,
            "production_packet": packet,
            "narrator_memory": _memory_view(memory_payloads, "narrator"),
        }
        style = production.get("prose_style") or resolve_prose_style(run.generation_context)
        if isinstance(style, dict) and style:
            writer_payload["prose_style"] = style
        # 修订信息进 payload，system 保持同前缀（避免整章重写打断缓存）
        writer_system = (
            _script_writer_system(
                style if isinstance(style, dict) else None,
                chapter_no=int(run.chapter_no),
            )
            + "若 payload 含 previous_draft/revision_instruction，"
            "请针对意见修改旧稿，不要凭空重写整章；"
            "不得改写剧本因果终点与 must_land_beats；不得引入弃选剧本情节。"
        )
        if prior_draft and revision_instruction:
            writer_payload["previous_draft"] = prior_draft
            writer_payload["revision_instruction"] = revision_instruction
            writer_payload["is_revision"] = True
        written = await call(
            SceneText,
            writer_system,
            writer_payload,
        )
        take = _ensure_script_take(production, packet)
        take["brief"] = _script_brief_from_packet(
            production, packet, prose_style=style if isinstance(style, dict) else None
        )
        prose = written.content.strip()
        take["content"] = prose
        take["prose_versions"] = [*list(take.get("prose_versions") or []), prose]
        take["scene_state"] = SceneRunState.DIRECTOR_VIEW.value
        take["artifact"] = SceneArtifact.PROSE.value
        # 消费掉意见，避免下一轮又当首稿指令
        take["revision_instruction"] = ""
        if run.title in {"", f"第{run.chapter_no}章"}:
            title_line = str((packet.get("script") or {}).get("title_line") or "").strip()
            if title_line:
                run.title = title_line
        production["phase"] = "VALIDATE"
    elif phase == "VALIDATE":
        take = (production.get("takes") or [None])[-1]
        packet = sp.get("production_packet") or {}
        if not take or not take.get("content"):
            raise ProductionStopped("核验缺少正文")
        from regent.novel.domain.repair_locate import build_scene_layout

        _scene_cards = list((sp.get("scene_plan") or {}).get("cards") or [])
        _scene_texts = list(sp.get("scene_texts") or [])
        _layout = build_scene_layout(scene_texts=_scene_texts, cards=_scene_cards)
        report = await call(
            ScriptChapterValidation,
            "核验正文是否兑现选定剧本的关键节拍与代价，有无越权新增事实。"
            "突破剧本终点或引入弃选路线记入 hard_fails。"
            "hard_fails 每条尽量带可检索摘录，格式：…摘录「原文片段」…；"
            "若能判断场次，可填 located_issues（scene_ids/evidence_quotes）。"
            "场次必须对照 payload.scene_layout（scene_index / scene_id / 字符区间），禁止自行猜分场。"
            "抽取有逐字 quote 的事实；你不负责戏剧决策，也不改写正文。",
            {
                "prose": take["content"],
                "selected_script": packet.get("script"),
                "must_land_beats": packet.get("must_land_beats") or [],
                "rejected": sp.get("rejected") or {},
                "canon": run.generation_context.get("canon", []),
                "scene_hard_fails": sp.get("scene_hard_fails") or [],
                "scene_layout": _layout,
                "scene_plan": {
                    "cards": [
                        {
                            "scene_id": (c or {}).get("scene_id")
                            if isinstance(c, dict)
                            else getattr(c, "scene_id", ""),
                            "purpose": (c or {}).get("purpose")
                            if isinstance(c, dict)
                            else getattr(c, "purpose", ""),
                        }
                        for c in _scene_cards
                    ]
                },
            },
        )
        # 场记问题与章节核验问题：script 整章修订；script_scene 不整章假返工
        scene_fails = list(sp.get("scene_hard_fails") or [])
        chapter_fails = list(report.hard_fails)
        from regent.novel.application import commons_audit as ca

        commons_rails = list(run.generation_context.get("commons_rails") or [])
        det = ca.deterministic_commons_issues(
            str(take.get("content") or ""),
            commons_rails=commons_rails,
            chapter_no=int(run.chapter_no),
        )
        chapter_fails = ca.merge_commons_into_issues(chapter_fails, det)
        # 明显错稿前置硬拦（人称/操作员代号/题文/金手指外显/整段复读）
        style = production.get("prose_style") or resolve_prose_style(run.generation_context)
        front_fails = front_gate_hard_fails(
            str(take.get("content") or ""),
            prose_style=style if isinstance(style, dict) else {},
            title=str(run.title or ""),
            cast=production.get("cast") or {},
            production_packet=packet if isinstance(packet, dict) else {},
            chapter_no=int(run.chapter_no),
            world_bible=(
                run.generation_context.get("world_bible")
                if isinstance(run.generation_context.get("world_bible"), dict)
                else None
            ),
            reader_contract=(
                run.generation_context.get("reader_contract")
                if isinstance(run.generation_context.get("reader_contract"), dict)
                else None
            ),
        )
        if front_fails:
            chapter_fails = list(dict.fromkeys([*chapter_fails, *front_fails]))
        all_fails = chapter_fails + scene_fails
        take["validation"] = {
            **report.model_dump(mode="json"),
            "passed": not all_fails and bool(report.facts),
            "issues": list(all_fails),
            "commons_issues": [x for x in all_fails if x.startswith("[commons:")],
            "front_gate_issues": [x for x in all_fails if str(x).startswith(("[pov:", "[front:"))],
            "facts": [f.model_dump(mode="json") for f in report.facts],
        }
        if scene_fails:
            # 场记硬问题未清：不得带病验收（正常应在 AUDIT 已停；此处兜底）
            _save(run, production)
            raise ProductionStopped(f"场记硬问题未清，拒绝验收：{'；'.join(scene_fails[:4])}")
        if chapter_fails:
            if proto == PROTOCOL_SCRIPT_SCENE:
                commons_only = [x for x in chapter_fails if str(x).startswith("[commons:")]
                other = [x for x in chapter_fails if not str(x).startswith("[commons:")]
                # 顾问/金手指字面：可 soft。复读/段重复不得 soft——会把双版本带进 DONE。
                softable = [
                    x
                    for x in chapter_fails
                    if str(x).startswith(("[commons:", "[front:power_surface]"))
                ]
                if softable and len(softable) == len(chapter_fails):
                    if commons_only and not other:
                        sp.setdefault("validate_soft_commons", []).extend(commons_only)
                        take["validation"]["soft_commons"] = commons_only
                    power_hits = [
                        x for x in softable if str(x).startswith("[front:power_surface]")
                    ]
                    if power_hits:
                        sp.setdefault("validate_soft_power", []).extend(power_hits)
                        take["validation"]["soft_power_surface"] = power_hits
                    take["validation"]["passed"] = bool(report.facts)
                    production["phase"] = "ACCEPT_CHAPTER"
                else:
                    repairs = int(sp.get("chapter_validate_repairs") or 0)
                    texts = list(sp.get("scene_texts") or [])
                    from regent.novel.domain.repair_locate import (
                        content_hash as _chash,
                    )
                    from regent.novel.domain.repair_locate import (
                        verify_repair_progress,
                    )

                    prev_ticket = sp.get("pending_repair_ticket") or {}
                    chapter_now = str(take.get("content") or "")
                    if repairs >= 1 and prev_ticket:
                        verify = verify_repair_progress(
                            ticket=prev_ticket,
                            chapter_content=chapter_now,
                            remaining_fails=list(other or chapter_fails),
                        )
                        sp["repair_verify"] = verify
                        base_hash = str(prev_ticket.get("base_content_hash") or "")
                        if (
                            not verify.get("progress")
                            and base_hash
                            and _chash(chapter_now) == base_hash
                        ):
                            _save(run, production)
                            raise ProductionStopped(
                                "修订无进展（正文未变且问题仍在），停止重试："
                                + "；".join((other or chapter_fails)[:4])
                            )
                    if repairs < 2 and texts:
                        sp["chapter_validate_repairs"] = repairs + 1
                        # 定位到真实失败场；无法定位则停机，禁止默认修末场
                        _begin_located_scene_repair(
                            production,
                            sp,
                            fails=list(other or chapter_fails),
                            instruction_prefix="章节核验未过，请改本场：",
                            take=take,
                            model_located=list(report.located_issues or []),
                        )
                        _save(run, production)
                        return False
                    _save(run, production)
                    raise ProductionStopped(
                        "章节核验硬失败，分场臂不整章重写：" + "；".join(chapter_fails[:4])
                    )
            else:
                # script 整章臂：修订已组装正文（带旧稿，非从零重写）
                _bump_creative_repair(production, sp)
                take["revision_instruction"] = "；".join(chapter_fails[:6])
                production["phase"] = "WRITE_CHAPTER"
        else:
            production["phase"] = "ACCEPT_CHAPTER"
    elif phase == "ACCEPT_CHAPTER":
        take = (production.get("takes") or [None])[-1]
        if not take or not take.get("content"):
            raise ProductionStopped("创作验收缺少正文")
        # pick 归因需要比较材料：只给选定稿却不给候选，无法判断"有更好候选却选错"。
        candidate_summaries = {
            cid: {
                "end_change": (c or {}).get("end_change"),
                "cost": (c or {}).get("cost"),
                "cost_type": (c or {}).get("cost_type"),
                "power_payoff": (c or {}).get("power_payoff"),
                "turning_point": (c or {}).get("turning_point"),
            }
            for cid, c in (sp.get("candidates") or {}).items()
        }
        accept = await call(
            ChapterCreativeAccept,
            "你是总导演，做整章创作验收（不是事实核验器）。"
            "判断读者是否愿意继续、能否代入主角权衡、金手指是否兑现。"
            "若失败，fault 三选一：supply=候选本就没有好戏；"
            "pick=有更好候选却选错（需对照 candidates）；render=剧本可拍但正文写坏。"
            "通过则 fault=ok。",
            {
                "prose": take["content"],
                "selected_id": sp.get("selected_id"),
                "direction": sp.get("choice"),
                "candidates": candidate_summaries,
                "validation": take.get("validation"),
            },
        )
        sp["creative_accept"] = accept.model_dump(mode="json")
        production["decisions"].append({"phase": phase, **accept.model_dump(mode="json")})
        fault = str(accept.fault or "").strip().lower()
        if fault in {"supply", "pick", "render"}:
            sp.setdefault("fault_taxonomy", {})[fault] = True
        if not accept.accept:
            if fault in {"supply", "pick"}:
                _bump_creative_repair(production, sp)
                # 保留验收意见，重生编剧时必须带上，否则重复生成同类失败稿。
                sp["last_reject_reason"] = accept.notes or f"创作验收判为 {fault}"
                sp["last_reject_weakness"] = ""
                sp["candidates"] = {}
                sp["rejected"] = {}
                sp["selected_id"] = ""
                sp["choice"] = None
                sp["production_packet"] = None
                sp["assignment"] = {}
                sp["assignment_goal"] = ""
                # 分场中间态一并清空，避免旧场正文混入重生轮
                for k in (
                    "scene_plan",
                    "scene_texts",
                    "scene_audits",
                    "scene_hard_fails",
                    "working_summary",
                    "scene_revision_instruction",
                    "pending_scene_beat_evidence",
                    "scene_state_trail",
                    "scene_entry_summaries",
                    "pending_repair_ticket",
                    "located_issues",
                ):
                    sp.pop(k, None)
                sp["scene_index"] = 0
                # 重生轮从已确认状态重开，不另起空字典
                confirmed = deepcopy(
                    production.get("working_state")
                    or run.generation_context.get("actual_state")
                    or {}
                )
                sp["working_state"] = confirmed
                production["takes"] = []
                production["accepted"] = []
                production["phase"] = "BRIEF"
            elif proto == PROTOCOL_SCRIPT_SCENE:
                # render：按定位修订；无法定位则停机。复读不得 soft 放行。
                notes = str(accept.notes or "")
                render_repairs = int(sp.get("creative_render_repairs") or 0)
                if render_repairs < 1 and list(sp.get("scene_texts") or []):
                    sp["creative_render_repairs"] = render_repairs + 1
                    validation = take.get("validation") or {}
                    locate_fails = list(
                        validation.get("issues")
                        or validation.get("front_gate_issues")
                        or []
                    )
                    if notes:
                        locate_fails = [*locate_fails, notes]
                    if not locate_fails:
                        locate_fails = [
                            "[front:redundant] " + (notes or "render 未过，需去重/统一事实")
                        ]
                    _begin_located_scene_repair(
                        production,
                        sp,
                        fails=locate_fails,
                        instruction_prefix="创作验收 render 未过，请改本场：",
                        take=take,
                    )
                    _save(run, production)
                    return False
                _save(run, production)
                raise ProductionStopped(
                    "创作验收未通过（render），分场臂不整章重写："
                    + (notes or fault or "render")
                )
            else:
                _bump_creative_repair(production, sp)
                take["revision_instruction"] = accept.notes or "加强代入与钩子兑现"
                production["phase"] = "WRITE_CHAPTER"
        if accept.accept:
            # 仅选定事实可提交；弃选永不入正典。
            facts = list((take.get("validation") or {}).get("facts") or [])
            take["status"] = "ACCEPTED"
            take["scene_state"] = SceneRunState.ACCEPTED.value
            take["validation"] = {
                **(take.get("validation") or {}),
                "passed": True,
                "issues": [],
                "facts": facts,
            }
            production["accepted"] = [len(production["takes"]) - 1]
            run.content = take["content"]
            run.word_count = len(run.content)
            # 全章通过：提交同一份核验后状态，不再维护两份工作状态
            if proto == PROTOCOL_SCRIPT_SCENE and sp.get("working_state") is not None:
                production["working_state"] = deepcopy(sp["working_state"])
            _RUNTIME.validate(
                director_command(
                    CommandKind.ASSEMBLE_CHAPTER,
                    command_id=_command_id(production, run, "assemble"),
                    input_version=_input_version(run),
                    scene_index=1,
                    take_no=take["take_no"],
                    evidence=[run.content[:200]],
                ),
                RuntimeState(
                    scene_state=SceneRunState.ACCEPTED.value,
                    input_version=_input_version(run),
                    scene_index=1,
                    take_no=take["take_no"],
                    has_prose=True,
                ),
            )
            take["assembled"] = True
            production["scene_index"] = 1
            production["phase"] = "DONE"
    else:
        raise ProductionStopped(f"剧本协议阶段尚未实现: {phase}")

    _save(run, production)
    return production["phase"] == "DONE"
