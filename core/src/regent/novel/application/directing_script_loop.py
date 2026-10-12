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
    RoleSelfReview,
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
    acceptance_plan_issues,
    assemble_production_packet,
    assert_rejected_isolated,
    assign_acceptance_to_scenes,
    derive_acceptance_items,
    empty_script_state,
    ensure_acceptance_assigned,
    normalize_assignment_board,
    normalize_chapter_validation_fails,
    route_script_hive,
    task_for_slot,
    validation_system_contract,
)
from regent.novel.domain.continuity_ledger import (
    apply_delta as apply_ledger_delta,
)
from regent.novel.domain.continuity_ledger import empty_ledger
from regent.novel.domain.continuity_ledger import ledger_to_text_summary
from regent.novel.domain.states import SceneArtifact, SceneRunState
from regent.novel.domain.world_bible import (
    resolve_prose_style,
)
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel


def _trim_context_for_writer(ctx: dict[str, Any]) -> dict[str, Any]:
    """执笔白名单（工作包3.4）：移除大段全文入口，只保留摘要与元数据。

    移除：recent_chapters[*].content、previous_handoff.tail_excerpt 以外的大段内容。
    保留：structural metadata、summaries、ledger、facts。
    """
    import copy

    trimmed = copy.deepcopy(ctx)
    for ch in trimmed.get("recent_chapters") or []:
        if isinstance(ch, dict):
            ch.pop("content", None)
            ch.pop("body", None)
    handoff = trimmed.get("previous_handoff")
    if isinstance(handoff, dict):
        for key in list(handoff):
            if key not in (
                "chapter_no",
                "content_hash",
                "tail_excerpt",
                "actual_state",
                "continuity_ledger",
                "review_matches_final",
            ):
                handoff.pop(key, None)
    return trimmed


def _longest_common_substring(a: str, b: str, *, min_len: int = 80) -> str:
    """找两个字符串的最长公共子串（>=min_len 才返回）。

    用于工作包3.3 文本重复检测：识别长连续复用（照抄/近似段落）。
    """
    if not a or not b or min_len < 1:
        return ""
    la, lb = len(a), len(b)
    best = ""
    for i in range(la):
        for j in range(lb):
            k = 0
            while i + k < la and j + k < lb and a[i + k] == b[j + k]:
                k += 1
            if k >= min_len and k > len(best):
                best = a[i : i + k]
    return best


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


def _audit_cont_deferrable(gate: Any) -> bool:
    """画面层：仅连续性阻断时可递延到责编，不得卡死后续场。

    缺 must 节拍 / 模型硬失败（改因果、跳场等）仍硬停。
    """
    if getattr(gate, "critical_missed", None):
        return False
    hard = [str(h) for h in (getattr(gate, "hard_fails", None) or []) if str(h).strip()]
    if hard:
        return False
    return bool(getattr(gate, "continuity_block", False))


def _append_gate_debt(
    sp: dict[str, Any],
    *,
    scene_id: str,
    tags: list[str],
    kind: str = "continuity",
    from_phase: str = "AUDIT_SCENE",
) -> None:
    """导演层递延债务：责编 REVIEW 合并并尝试修；修满可 coerce 留痕。

    统一写入 gate_debts 列表，由 kind 字段区分 continuity / prose_front。
    """
    clean = [str(t) for t in tags if str(t).strip()]
    if not clean:
        return
    debts = sp.setdefault("gate_debts", [])
    debts.append(
        {
            "owner": "review",
            "kind": kind,
            "scene_id": scene_id,
            "tags": clean[:8],
            "from_phase": from_phase,
        }
    )


def _script_cards(sp: dict[str, Any]) -> list[Any]:
    plan = sp.get("scene_plan") or {}
    return list(plan.get("cards") or [])


def _normalize_msg(s: str) -> str:
    import re

    return re.sub(r"[\s\W_]+", "", str(s or ""), flags=re.UNICODE)


def _msg_overlap(a: str, b: str) -> bool:
    """问题文案是否有实质重叠（用于绑定模型定位到待修 hard fail）。"""
    na, nb = _normalize_msg(a), _normalize_msg(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    window = 8
    if len(na) >= window:
        for i in range(0, len(na) - window + 1):
            if na[i : i + window] in nb:
                return True
    if len(nb) >= window:
        for i in range(0, len(nb) - window + 1):
            if nb[i : i + window] in na:
                return True
    return False


def _extract_fail_code(msg: str) -> str:
    import re

    m = re.match(r"^\[([a-z0-9_:-]+)\]", str(msg or "").strip())
    return m.group(1) if m else ""


def _model_binds_to_unsolved_fail(
    data: dict[str, Any],
    *,
    fails: list[str],
    located: list[Any],
) -> bool:
    """模型定位是否对应某个尚未解决的 hard fail。

    关闭条件（C2）：通过稳定 issue_id 或明确映射绑定；
    未知/无关条目不能改变修订目标。
    """
    code = str(data.get("code") or "").strip()
    msg = str(data.get("message") or "").strip()
    iid = str(data.get("issue_id") or "").strip()

    located_ids = {getattr(i, "issue_id", "") for i in located}
    # 已有确定性场景归属的问题身份
    located_with_scenes = set()
    for i in located:
        if getattr(i, "scene_ids", None):
            located_with_scenes.add(
                (str(getattr(i, "code", "")), _normalize_msg(getattr(i, "message", ""))[:40])
            )

    for fail in fails:
        fail_s = str(fail or "").strip()
        if not fail_s:
            continue
        fail_code = _extract_fail_code(fail_s)
        fail_key = (fail_code or "", _normalize_msg(fail_s)[:40])
        # 该 fail 已有确定性场景归属 → 模型不得再改写其目标
        already = fail_key in located_with_scenes
        if not already and fail_code:
            already = any(
                c == fail_code and _msg_overlap(fail_s, m)
                for c, m in (
                    (
                        str(getattr(i, "code", "")),
                        str(getattr(i, "message", "")),
                    )
                    for i in located
                    if getattr(i, "scene_ids", None)
                )
            )
        # issue_id 明确出现在 fail 文案中
        if iid and iid in fail_s:
            if not already:
                return True
            continue
        # code 必须一致（双方都有 code 时）；文案必须实质重叠
        if code and fail_code and code != fail_code:
            continue
        if msg and _msg_overlap(msg, fail_s):
            if not already:
                return True

    # 可补齐确定性未定位条目（scene_ids 为空）
    for issue in located:
        if getattr(issue, "scene_ids", None):
            continue
        issue_id = str(getattr(issue, "issue_id", "") or "")
        issue_code = str(getattr(issue, "code", "") or "")
        issue_msg = str(getattr(issue, "message", "") or "")
        if iid and iid == issue_id:
            return True
        if code and issue_code and code != issue_code:
            continue
        if msg and _msg_overlap(msg, issue_msg):
            return True
        if iid and iid in located_ids and issue_msg and _msg_overlap(msg, issue_msg):
            return True
    return False


def _beat_scene_map(cards: list[Any]) -> dict[str, str]:
    """beat_id → scene_id 映射（缺失节拍定位必须用它，不得模糊搜 message）。"""
    mapping: dict[str, str] = {}
    for c in cards:
        sid = str(
            (c.get("scene_id") if isinstance(c, dict) else getattr(c, "scene_id", ""))
            or ""
        )
        if not sid:
            continue
        beats = (
            c.get("beats")
            if isinstance(c, dict)
            else getattr(c, "beats", []) or []
        )
        for b in beats:
            bid = str(
                (b.get("beat_id") if isinstance(b, dict) else getattr(b, "beat_id", ""))
                or ""
            )
            if bid:
                mapping[bid] = sid
    return mapping


def _verify_model_location(
    *,
    data: dict[str, Any],
    cards: list[Any],
    texts: list[str],
    full_chapter: str,
) -> tuple[str, ...] | None:
    """核验模型定位：合法 scene_id + 引文逐条归属 + hash 一致；否则 None。

    关闭条件（C2）：
    - 每条进入票据的引文必须归属至少一个所声明场景；
      每个声明场景也必须有对应证据。假引文混入 → 整条拒绝。
    - 缺失节拍优先 beat_id→scene_id 映射。
    """
    from regent.novel.domain.repair_locate import content_hash as _chash
    from regent.novel.domain.scene_card import evidence_grounded

    sids = tuple(str(x) for x in (data.get("scene_ids") or []) if str(x))
    if not sids:
        return None
    scene_idx: dict[str, int] = {}
    for i, c in enumerate(cards):
        sid = str(
            (c.get("scene_id") if isinstance(c, dict) else getattr(c, "scene_id", ""))
            or ""
        )
        if sid:
            scene_idx[sid] = i
    known = set(scene_idx)
    if any(sid not in known for sid in sids):
        return None
    model_hash = str(data.get("content_hash") or "").strip()
    if model_hash and model_hash != _chash(full_chapter):
        return None

    quotes = tuple(str(q) for q in (data.get("evidence_quotes") or []) if str(q))

    if not quotes:
        # 缺失节拍：仅允许显式、合法 beat_ids → scene 映射（D2）
        # 未知 ID / 混合无效 ID → 整条拒绝；不以 message 子串代替身份
        beat_map = _beat_scene_map(cards)
        beat_ids = [str(b).strip() for b in (data.get("beat_ids") or []) if str(b).strip()]
        if not beat_ids:
            return None
        if any(b not in beat_map for b in beat_ids):
            return None
        mapped = tuple(dict.fromkeys(beat_map[b] for b in beat_ids))
        return mapped if mapped else None

    # 有引文：每条引文必须归属至少一个声明场景；假引文 → 整条拒绝
    quote_scenes: list[set[str]] = []
    for q in quotes:
        grounded_in: set[str] = set()
        for sid in sids:
            idx = scene_idx.get(sid)
            if idx is None or idx >= len(texts):
                continue
            if evidence_grounded(q, texts[idx]):
                grounded_in.add(sid)
        if not grounded_in:
            return None
        quote_scenes.append(grounded_in)
    # 每个声明场景必须至少有一条对应证据
    for sid in sids:
        if not any(sid in gs for gs in quote_scenes):
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

    模型定位只作核验后的补齐：
    1) 必须绑定某个尚未解决的 hard fail（issue_id / code+文案重叠）；
    2) 合法 scene_id + 引文逐条归属 + hash 一致；
    3) 不得覆盖已有确定性定位。
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
    for raw in model_located or []:
        try:
            data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        except Exception:
            continue
        # 无关问题不得改变修订目标
        if not _model_binds_to_unsolved_fail(data, fails=fails, located=located):
            continue
        verified_sids = _verify_model_location(
            data=data, cards=cards, texts=texts, full_chapter=full_chapter
        )
        if not verified_sids:
            continue
        code = str(data.get("code") or "model")
        msg = str(data.get("message") or data.get("issue_id") or code)
        # 同一问题已被确定性定位 → 不覆盖
        if any(
            i.scene_ids
            and (
                (str(data.get("issue_id") or "") and str(data.get("issue_id")) == i.issue_id)
                or (code == i.code and _msg_overlap(msg, i.message))
            )
            for i in located
        ):
            continue
        # 只保留接地引文（核验已保证每条可归属；此处再过滤空串）
        quotes = tuple(str(q) for q in (data.get("evidence_quotes") or []) if str(q))
        located.append(
            LocatedIssue(
                issue_id=str(data.get("issue_id") or f"{code}#model"),
                code=code,
                severity="hard"
                if str(data.get("severity") or "hard") == "hard"
                else "soft",
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
        schema: type[T],
        system: str,
        payload: dict[str, Any],
        *,
        repair_no: int = 0,
        phase_name: str | None = None,
    ) -> T:
        # 剧本臂始终用场景感知标识：审校回退后 takes 非空，若仍走 take 维度
        # _command_id，会忽略 scene_id/srep，第二场与重写撞幂等键。
        used_phase = phase_name or phase
        command_id = _script_scene_command_id(run, production, sp, used_phase)
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
            f"script:{used_phase}",
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
        "previous_handoff",
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
        acc_items_raw = derive_acceptance_items(selected, choice)
        
        # 提取剧本事件清单（LLM 驱动，支持幂等恢复）
        from regent.novel.domain.script_events import extract_script_events
        script_events_result = await extract_script_events(
            session=session,
            provider=provider,
            work=work,
            run=run,
            production=production,
            script_dict=selected.model_dump(),
        )
        sp["script_events"] = [e.model_dump() for e in script_events_result.events]
        from regent.novel.domain.script_events import validate_script_events

        _se_errs = validate_script_events(script_events_result.events)
        if _se_errs:
            raise ProductionStopped("剧本事件清单不合法：" + "；".join(_se_errs[:4]))
        
        from regent.novel.domain.chapter_handoff import (
            initial_entry_from_previous,
            merge_continuation_gaps,
        )

        prev_handoff = run.generation_context.get("previous_handoff")
        has_previous_handoff = isinstance(prev_handoff, dict) and bool(
            str(prev_handoff.get("content_hash") or "")
        )
        review_seed = [
            str(item)
            for item in (sp.get("review_plan_repair") or [])
            if str(item).strip()
        ]
        compile_repair: list[str] = []
        if review_seed:
            compile_repair.append(
                "整章审校指出计划层问题，必须重分场而不是只改正文："
                + "；".join(review_seed[:4])
            )
        plan_draft = None
        plan_issues: list[str] = []

        async def _compile_self_issues(plan_obj: Any) -> list[str]:
            if sp.get("compile_self_review_done"):
                return []
            review = await call(
                RoleSelfReview,
                "你是刚才分场的总导演，只审核自己刚交出的分场，不重写正文。"
                "对照 previous_handoff 与本章已排事件："
                "后场不得把已经发生过的事件再排成 occurrence；"
                "时间或地点跳跃必须有 transition_note；"
                "有 previous_handoff 时 continuation_mode 必须是 direct、time_jump 或 pov_switch。"
                "能改计划就 passed=false，并在 revision_note 写清要改的场次。"
                "passed=true 只表示这份分场可以交给执笔。",
                {
                    "scene_plan": plan_obj.model_dump(),
                    "previous_handoff": prev_handoff if has_previous_handoff else None,
                    "script_events": sp.get("script_events") or [],
                    "chapter_goal": getattr(plan_obj, "chapter_goal", "") or "",
                },
                phase_name="SELF_COMPILE",
            )
            sp["compile_self_review_done"] = True
            sp["compile_self_review"] = review.model_dump()
            if review.passed:
                return []
            note = (review.revision_note or "").strip()
            issues = [str(item).strip() for item in review.issues if str(item).strip()]
            if note:
                issues.append(note)
            return issues or ["分场自审未通过"]

        def _with_continuation(plan_obj: Any, hard_issues: list[str]) -> list[str]:
            return merge_continuation_gaps(
                hard_issues,
                mode=str(getattr(plan_obj, "continuation_mode", "") or ""),
                note=str(getattr(plan_obj, "transition_note", "") or ""),
                has_previous=has_previous_handoff,
            )

        for repair_no in range(MAX_PLAN_REPAIRS + 1):
            compile_payload: dict[str, Any] = {
                **base_payload,
                "selected_script": selected.model_dump(),
                "direction": choice.model_dump(),
                "skeleton_plan": skeleton.model_dump(),
                "acceptance_items": [i.model_dump(mode="json") for i in acc_items_raw],
                "script_events": sp.get("script_events") or [],
                "previous_handoff": prev_handoff if has_previous_handoff else None,
                "max_scenes": 4,
                "coverage_note": (
                    "节拍正文须嵌入选定剧本 turning_point / cost / end_change 的关键短语，"
                    "以及 must_land_beats 的可读效果，便于覆盖校验；可改写但勿整段蒸发。"
                ),
            }
            if compile_repair:
                compile_payload["repair_instructions"] = compile_repair
                # 审校打回重分场时旧计划已清空，第一轮还没有 previous_plan。
                if plan_draft is not None:
                    compile_payload["previous_plan"] = plan_draft.model_dump()
            plan_draft = await call(
                _ScenePlanDraft,
                "你是总导演，负责分场。把选定剧本拆成 2–4 张场景卡。"
                "每张卡写清 purpose/entry_state/protagonist_objective/opposition/"
                "core_choice/emotion_arc/exit_change。每个节拍有唯一 beat_id。"
                "必须覆盖原剧本的转折与代价。所有 scene_id/beat_id 必须唯一。"
                "按事件发生顺序分场；同一事件只能展开一次，换 beat_id 不等于新事件。"
                "不得在首场塞完整章再在后场重演；章末夜间收束应放在白天事件之后。"
                "每项 required 验收义务通过 acceptance_ids 指定唯一责任场，"
                "并在该场节拍写出相应行动或认知；后场只承接已成立状态。"
                "骨架仅供参考，可重新划分边界，禁止机械把未匹配义务塞进末场。"
                "若有 previous_plan 或 repair_instructions，应修复其重复事件、顺序和责任归属，不能只改 ID。"
                "若 payload 含 previous_handoff，continuation_mode 必须是 direct、time_jump 或 pov_switch。"
                "time_jump 与 pov_switch 必须写 transition_note，说明合法转换，不得添加前章正文没有的事实。"
                "direct 表示直接承接前章出口。没有 previous_handoff 时 continuation_mode 可留空或填 opening。"
                "节拍文字要能让人看出转折、代价与章末变化，不要只写气氛。"
                "每个场景必须通过 event_refs 引用 script_events 中的事件，"
                "每个 event_id 必须来自 script_events 清单，禁止自造事件 ID。"
                "同一 event_id 的 role=occurrence 只能出现在一个场景，"
                "后续场景只能以 consequence 或 reference 角色引用。",
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
            # 验收义务必须在开写前落到责任场次：计划软放行 + 整章硬拦 = 期限倒挂
            # 只映射责任，不由确定性代码把未知义务强塞到末场。
            acc_items, filled_cards = ensure_acceptance_assigned(
                acc_items_raw, list(plan_draft.cards)
            )
            plan_draft = plan_draft.model_copy(update={"cards": filled_cards})
            plan = ChapterScenePlan(
                chapter_no=int(run.chapter_no),
                cards=list(plan_draft.cards),
                chapter_goal=plan_draft.chapter_goal or selected.end_change,
                continuity_notes=plan_draft.continuity_notes,
            )
            plan_issues = validate_scene_plan(plan, selected, choice, max_scenes=4)
            acc_hard = acceptance_plan_issues(acc_items)
            known_acc_ids = {i.item_id for i in acc_items_raw}
            acc_hard.extend(
                f"acceptance_unknown:{card.scene_id}:{item_id}"
                for card in plan_draft.cards
                for item_id in card.acceptance_ids
                if item_id not in known_acc_ids
            )
            # 事件引用硬闸：清单完备 + 唯一所有权 + 义务落场
            from regent.novel.domain.script_events import (
                ground_orphan_event_roles,
                validate_plan_event_refs,
            )

            def _ground_plan(plan_obj: Any) -> Any:
                grounded = ground_orphan_event_roles(
                    [card.model_dump() for card in plan_obj.cards],
                    sp.get("script_events") or [],
                )
                return plan_obj.model_copy(
                    update={"cards": [SceneCard.model_validate(c) for c in grounded]}
                )

            plan_draft = _ground_plan(plan_draft)
            event_ref_errors = validate_plan_event_refs(
                [card.model_dump() for card in plan_draft.cards],
                sp.get("script_events") or [],
                acceptance_items=[i.model_dump(mode="json") for i in acc_items],
            )
            hard = _with_continuation(
                plan_draft,
                structural_plan_issues(plan_issues) + acc_hard + event_ref_errors,
            )
            if not hard:
                self_issues = await _compile_self_issues(plan_draft)
                if self_issues:
                    hard = self_issues
                else:
                    sp["acceptance_items"] = [i.model_dump(mode="json") for i in acc_items]
                    break
            
            # 如果有事件引用错误，触发 LLM 重分场
            if event_ref_errors and repair_no == 0:
                from regent.novel.domain.script_events import repartition_plan
                repartitioned_cards = await repartition_plan(
                    session=session,
                    provider=provider,
                    work=work,
                    run=run,
                    production=production,
                    plan_cards=[card.model_dump() for card in plan_draft.cards],
                    event_errors=event_ref_errors,
                    script_events=sp.get("script_events") or [],
                )
                # 用重分场后的计划替换当前计划，继续校验循环
                plan_draft = plan_draft.model_copy(
                    update={"cards": [SceneCard.model_validate(c) for c in repartitioned_cards]}
                )
                plan = ChapterScenePlan(
                    chapter_no=int(run.chapter_no),
                    cards=list(plan_draft.cards),
                    chapter_goal=plan_draft.chapter_goal or selected.end_change,
                    continuity_notes=plan_draft.continuity_notes,
                )
                # 重新校验
                plan_issues = validate_scene_plan(plan, selected, choice, max_scenes=4)
                acc_items, filled_cards = ensure_acceptance_assigned(
                    acc_items_raw, list(plan_draft.cards)
                )
                plan_draft = plan_draft.model_copy(update={"cards": filled_cards})
                plan = ChapterScenePlan(
                    chapter_no=int(run.chapter_no),
                    cards=list(plan_draft.cards),
                    chapter_goal=plan_draft.chapter_goal or selected.end_change,
                    continuity_notes=plan_draft.continuity_notes,
                )
                plan_issues = validate_scene_plan(plan, selected, choice, max_scenes=4)
                acc_hard = acceptance_plan_issues(acc_items)
                known_acc_ids = {i.item_id for i in acc_items_raw}
                acc_hard.extend(
                    f"acceptance_unknown:{card.scene_id}:{item_id}"
                    for card in plan_draft.cards
                    for item_id in card.acceptance_ids
                    if item_id not in known_acc_ids
                )
                plan_draft = _ground_plan(plan_draft)
                event_ref_errors = validate_plan_event_refs(
                    [card.model_dump() for card in plan_draft.cards],
                    sp.get("script_events") or [],
                    acceptance_items=[i.model_dump(mode="json") for i in acc_items],
                )
                hard = _with_continuation(
                plan_draft,
                structural_plan_issues(plan_issues) + acc_hard + event_ref_errors,
            )
                if not hard:
                    self_issues = await _compile_self_issues(plan_draft)
                    if self_issues:
                        hard = self_issues
                    else:
                        sp["acceptance_items"] = [i.model_dump(mode="json") for i in acc_items]
                        break
            
            compile_repair = [
                "上一版分场未通过：" + "；".join(hard[:6]) + "。",
                "修复事件重复和时间顺序；保留唯一 ID，补全空场，"
                "为每项 required 义务指定唯一 acceptance_ids 责任场，并覆盖转折与代价。",
                "若某事件只有 consequence、没有任何 occurrence，"
                "在最早引用它的场改成 occurrence，不要只标后果。",
            ]
            if acc_hard:
                missing = [
                    i.text for i in acc_items if i.required and not i.scene_ids
                ][:4]
                compile_repair.append(
                    "以下章末义务必须分配到具体场次的节拍/purpose/exit_change，"
                    "禁止只写气氛：" + "；".join(missing)
                )
        else:
            assert plan_draft is not None
            if hard:
                sp["compile_hard_issues"] = list(hard)
                sp["failed_scene_plan"] = plan_draft.model_dump()
                _save(run, production)
                raise ProductionStopped("分场覆盖校验失败：" + "；".join(hard[:4]))
            sp["acceptance_items"] = [i.model_dump(mode="json") for i in acc_items]
        soft = [i for i in plan_issues if i not in structural_plan_issues(plan_issues)]
        if soft:
            sp["compile_soft_issues"] = soft
        assert plan_draft is not None
        if "acceptance_items" not in sp:
            acc_items, filled_cards = ensure_acceptance_assigned(
                derive_acceptance_items(selected, choice),
                list(plan_draft.cards),
            )
            plan_draft = plan_draft.model_copy(update={"cards": filled_cards})
            sp["acceptance_items"] = [i.model_dump(mode="json") for i in acc_items]
        # 空 purpose 兜底：LLM 偶发空串，不再 ValidationError 炸掉整章
        for _c in plan_draft.cards:
            if not str(getattr(_c, "purpose", "") or "").strip():
                _c.purpose = (
                    plan_draft.chapter_goal
                    or selected.end_change
                    or "推进本场"
                )
        continuation_mode = str(getattr(plan_draft, "continuation_mode", "") or "")
        transition_note = str(getattr(plan_draft, "transition_note", "") or "")
        sp.pop("review_plan_repair", None)
        sp["scene_plan"] = {
            "cards": [c.model_dump() for c in plan_draft.cards],
            "chapter_goal": plan_draft.chapter_goal or selected.end_change,
            "continuity_notes": plan_draft.continuity_notes,
            "continuation_mode": continuation_mode,
            "transition_note": transition_note,
        }
        sp["continuation"] = {
            "mode": continuation_mode,
            "transition_note": transition_note,
        }
        sp["scene_index"] = 0
        sp["scene_texts"] = []
        # 本章入口只在首次分场快照一次；回退/重生都从这里恢复。
        # 绝不用失败草稿的出口（ASSEMBLE 写入 production.working_state）当新入口。
        entry = production.get("chapter_entry_state")
        if not isinstance(entry, dict):
            entry = deepcopy(
                production.get("working_state")
                or run.generation_context.get("actual_state")
                or {}
            )
            production["chapter_entry_state"] = deepcopy(entry)
        confirmed = deepcopy(entry)
        sp["working_state"] = confirmed
        sp["scene_state_trail"] = [deepcopy(confirmed)]
        sp["scene_exit_states"] = []
        # 连续性台账（工作包2）：与前章 handoff 恢复，或空台账。
        prev_ledger = (prev_handoff or {}).get("continuity_ledger")
        sp["continuity_ledger"] = (
            deepcopy(prev_ledger) if isinstance(prev_ledger, dict) else empty_ledger()
        )
        sp["continuity_ledger_trail"] = [deepcopy(sp["continuity_ledger"])]
        opening, opening_entries = initial_entry_from_previous(
            prev_handoff if has_previous_handoff else None
        )
        sp["working_summary"] = opening
        sp["scene_entry_summaries"] = opening_entries
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
        # 工作包3.1：分场前检——校验本场计划事件是否违反台账约束。
        ledger = sp.get("continuity_ledger") or empty_ledger()
        pre_scene_violations: list[str] = []
        for eref in card.event_refs:
            eid = str(eref.event_id or "")
            role = str(eref.role or "")
            if role == "occurrence" and "读取" in eid:
                from regent.novel.domain.continuity_ledger import check_prop_readable

                for pid in (ledger.get("props") or {}):
                    if pid in eid or eid in pid:
                        pre_scene_violations.extend(check_prop_readable(ledger, pid))
        if pre_scene_violations:
            sp.setdefault("ledger_violations", []).append(
                {"scene_id": card.scene_id, "phase": "pre_scene", "v": pre_scene_violations}
            )
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
        # 工作包3.4：移除大段全文入口，只保留摘要与元数据。
        write_payload: dict[str, Any] = {
            "context": _trim_context_for_writer(ordered_ctx),
            "cast": production.get("cast") or {},
            "user_guidance": run.user_guidance or {},
            "scene_card": card.model_dump(),
            "acceptance_items": [
                item for item in (sp.get("acceptance_items") or [])
                if card.scene_id in (item.get("scene_ids") or [])
            ],
            "entry_summary": entry_summary,
            "working_state": sp.get("working_state") or {},
            "continuity_ledger_summary": ledger_to_text_summary(
                sp.get("continuity_ledger") or {}
            ),
            "chapter_goal": plan.get("chapter_goal") or "",
            "continuation": {
                "mode": plan.get("continuation_mode") or "",
                "transition_note": plan.get("transition_note") or "",
            },
            "narrator_memory": _memory_view(memory_payloads, "narrator"),
            "target_chars": target,
        }
        if isinstance(style, dict) and style:
            write_payload["prose_style"] = style
        dedup_quotes = list(sp.get("chapter_dedup_quotes") or [])
        if dedup_quotes:
            prior_scenes = "\n\n".join(list(sp.get("scene_texts") or [])[:idx])
            write_payload["chapter_dedup_constraints"] = [
                {"quote": q, "present_in_prior_scenes": q in prior_scenes}
                for q in dedup_quotes
            ]
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
        sid = str(card.scene_id)
        # 工作包4：自审结果绑定正文哈希，内容变化时自审失效。
        from regent.novel.domain.chapter_handoff import content_hash

        current_hash = content_hash(scene_text)
        stored_review = sp.get(f"writer_self_reviewed:{sid}")
        stored_hash = sp.get(f"writer_self_reviewed_hash:{sid}")
        if stored_review and stored_hash != current_hash:
            sp.pop(f"writer_self_reviewed:{sid}", None)
            sp.pop(f"writer_self_reviewed_hash:{sid}", None)
            stored_review = None
        if not stored_review:
            review = await call(
                RoleSelfReview,
                "你是刚才写这场的执笔，只审核自己刚交的正文，不重写别的场。"
                "对照 entry_summary、working_state 与本场节拍："
                "不得重演入场摘要里已经完成的动作；"
                "伤势、器物、地点必须接上入场状态；"
                "没有动机的时间或地点跳跃要指出来；"
                "beat_evidence 的摘录必须是正文逐字，不能只在说明里出现。"
                "能改就 passed=false，revision_note 只写要改的句子。"
                "passed=true 只表示这场可以交给场记。",
                {
                    **write_payload,
                    "draft": scene_text,
                    "beat_evidence": written.beat_evidence,
                },
                phase_name="SELF_WRITE",
            )
            sp[f"writer_self_reviewed:{sid}"] = review.model_dump()
            sp[f"writer_self_reviewed_hash:{sid}"] = current_hash
            if not review.passed and int(sp.get(f"self_repairs:{sid}") or 0) < 1:
                sp[f"self_repairs:{sid}"] = 1
                texts = list(sp.get("scene_texts") or [])
                while len(texts) <= idx:
                    texts.append("")
                texts[idx] = scene_text
                sp["scene_texts"] = texts
                note = (review.revision_note or "").strip() or "；".join(
                    str(item) for item in review.issues[:3] if str(item).strip()
                )
                sp["scene_revision_instruction"] = f"自审未通过，只改这些问题：{note}"[:400]
                sp["pending_scene_beat_evidence"] = written.beat_evidence
                production["phase"] = "WRITE_SCENE"
                _save(run, production)
                return False
        if prior_draft and revision_instruction and sp.get("scene_texts"):
            sp["scene_texts"][idx] = scene_text
        else:
            sp.setdefault("scene_texts", []).append(scene_text)
        # 工作包3.3：文本重复检测——本场与前场/前章尾段比较，检测长连续复用。
        prior_texts = list(sp.get("scene_texts") or [])[:-1]
        prev_tail = ""
        for prev in reversed(run.generation_context.get("recent_chapters") or []):
            if isinstance(prev, dict):
                prev_tail = str(prev.get("tail_excerpt") or "")
                break
        repetition_warnings: list[str] = []
        for i, prior in enumerate(prior_texts):
            common = _longest_common_substring(scene_text, prior, min_len=80)
            if common:
                repetition_warnings.append(f"scene_{i}:len={len(common)}")
        if prev_tail:
            common = _longest_common_substring(scene_text, prev_tail, min_len=80)
            if common:
                repetition_warnings.append(f"prev_chapter_tail:len={len(common)}")
        if repetition_warnings:
            sp.setdefault("repetition_warnings", []).append(
                {"scene_id": card.scene_id, "warnings": repetition_warnings}
            )
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
        audit_system = (
            "你是场记，一次完成：节拍核验（landed 必须有 evidence 原文摘录）、"
            "连续性（与入场状态/上场结尾矛盾）、实际变化（计划中但正文未呈现的不算）。"
            "hard_fails 列出跳场、改因果终点、核心节拍完全缺失；每条尽量带摘录「…」。"
            "仅当问题是表述衔接、空状态误报或可软化的空间省略时，"
            "用前缀 [soft-cont:…] 写入 continuity_issues；真实事实矛盾不得 soft。"
            "注意：空入场状态只表示尚无已确认事实，不要把本场节拍要求的「发现/取出」"
            "写成 continuity 硬伤；真正矛盾须对照非空 working_state 或上场结尾。"
        )
        audit_payload = {
            "scene_card": card.model_dump(),
            "scene_text": scene_text,
            "beat_evidence": sp.get("pending_scene_beat_evidence") or {},
            "entry_summary": _audit_entry,
            "working_state": sp.get("working_state") or {},
            "prior_scene_ending": (
                texts[idx - 1][-300:] if idx > 0 and idx - 1 < len(texts) else ""
            ),
        }
        audit = await call(_SceneAudit, audit_system, audit_payload)
        # 工作包4：场记自审绑定正文+场记哈希，任一变化时自审失效。
        from regent.novel.domain.chapter_handoff import content_hash

        audit_hash = content_hash(scene_text + "|" + json.dumps(audit.model_dump(), ensure_ascii=False, sort_keys=True))
        stored_audit_review = sp.get(f"auditor_self_reviewed:{card.scene_id}")
        stored_audit_hash = sp.get(f"auditor_self_reviewed_hash:{card.scene_id}")
        if stored_audit_review and stored_audit_hash != audit_hash:
            sp.pop(f"auditor_self_reviewed:{card.scene_id}", None)
            sp.pop(f"auditor_self_reviewed_hash:{card.scene_id}", None)
            stored_audit_review = None
        if not stored_audit_review:
            review = await call(
                RoleSelfReview,
                "你是刚才做场记的人，只审核自己刚写的场记记录，不改正文。"
                "landed 的 evidence 必须是本场正文的逐字摘录；"
                "state_changes 只能记录本场正文确实发生的事，"
                "不得把计划里还没写出的事、或前场已经完成的动作记成这场的新变化。"
                "记录不合格就 passed=false，revision_note 写清要改的判定。",
                {
                    "scene_card": card.model_dump(),
                    "scene_text": scene_text,
                    "entry_summary": _audit_entry,
                    "audit": audit.model_dump(),
                },
                phase_name="SELF_AUDIT",
            )
            sp[f"auditor_self_reviewed:{card.scene_id}"] = review.model_dump()
            sp[f"auditor_self_reviewed_hash:{card.scene_id}"] = audit_hash
            if (
                not review.passed
                and int(sp.get(f"auditor_self_repairs:{card.scene_id}") or 0) < 1
            ):
                sp[f"auditor_self_repairs:{card.scene_id}"] = 1
                note = (review.revision_note or "").strip() or "；".join(
                    str(item) for item in review.issues[:3] if str(item).strip()
                )
                audit = await call(
                    _SceneAudit,
                    audit_system + " 这是自审后的一次订正，按 self_review_note 改记录。",
                    {**audit_payload, "self_review_note": note},
                    repair_no=1,
                )
        # 程序门控：补全 must 判定 → 证据接地 → 连续性/硬失败（与实验共用）
        must_ids = {b.beat_id for b in card.beats if b.must_show}
        _prior_ending = (
            texts[idx - 1][-300:] if idx > 0 and idx - 1 < len(texts) else ""
        )
        # 结构化前场出口：仅 scene_exit_states[idx-1]；不得用 trail 入口冒充新值
        _prior_exit: dict[str, Any] | None = None
        _exits = sp.get("scene_exit_states")
        if isinstance(_exits, list) and idx > 0 and idx - 1 < len(_exits):
            cand = _exits[idx - 1]
            if isinstance(cand, dict) and cand:
                _prior_exit = cand
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
            prior_exit_state=_prior_exit,
        )
        sp.setdefault("scene_audits", {})[card.scene_id] = audit.model_dump()

        # 工作包3.2：正文后检——校验实际事件是否越界。
        from regent.novel.domain.scene_boundary import check_scene_boundary

        boundary_violations = check_scene_boundary(
            scene_id=card.scene_id,
            actual_events=[e.model_dump() for e in audit.actual_events],
            scene_plan=plan,
            ledger=sp.get("continuity_ledger") or {},
        )
        if boundary_violations:
            sp.setdefault("boundary_violations", []).extend(
                [{"scene_id": card.scene_id, "v": v} for v in boundary_violations]
            )

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

        # 修满仍阻断：缺拍/改因果硬停；空入场误报 soft；仅连续性递延责编。
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
                if post_review:
                    sp["post_review_rewrite"] = False
            elif _audit_cont_deferrable(gate):
                tags = gate.hard_fail_tags(card.scene_id)
                _append_gate_debt(
                    sp,
                    scene_id=str(card.scene_id),
                    tags=tags,
                    kind="continuity",
                    from_phase="AUDIT_SCENE",
                )
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
        # 台账增量（工作包2）：原子应用，违规时记录但不阻断（首轮并行，不替代旧链路）。
        ledger = sp.get("continuity_ledger") or empty_ledger()
        if audit.ledger_delta is not None:
            delta_dict = audit.ledger_delta.model_dump(mode="json")
            new_ledger, violations = apply_ledger_delta(
                ledger,
                delta_dict,
                scene_id=audit.scene_id,
                prose_hash="",
                event_id="",
            )
            if violations:
                sp.setdefault("ledger_violations", []).extend(
                    [{"scene_id": audit.scene_id, "v": v} for v in violations]
                )
            else:
                ledger = new_ledger
        sp["continuity_ledger"] = ledger
        sp.setdefault("continuity_ledger_trail", []).append(deepcopy(ledger))
        # 结构化场出口：供后场证明「已接受新值」而非「旧值未出现」
        exits = list(sp.get("scene_exit_states") or [])
        while len(exits) < idx:
            exits.append({})
        if len(exits) == idx:
            exits.append(deepcopy(next_state))
        else:
            exits[idx] = deepcopy(next_state)
        sp["scene_exit_states"] = exits
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
            f"上场结尾（已完成，禁止重演）：{scene_text[-300:]}\n"
            f"硬约束：本场不得再次「醒来/睁眼」重写上场已写完的瞬间；"
            f"上场结尾动作与发现已是既成事实，只可承接，不可当新发现重述。\n"
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
        if sp.get("continuity_ledger") is not None:
            production["continuity_ledger"] = deepcopy(sp["continuity_ledger"])
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
        _beat_map = {
            str(
                (
                    b.get("beat_id")
                    if isinstance(b, dict)
                    else getattr(b, "beat_id", "")
                )
                or ""
            ): str(
                (
                    c.get("scene_id")
                    if isinstance(c, dict)
                    else getattr(c, "scene_id", "")
                )
                or ""
            )
            for c in _scene_cards
            for b in (
                (
                    c.get("beats")
                    if isinstance(c, dict)
                    else getattr(c, "beats", []) or []
                )
                or []
            )
            if str(
                (
                    b.get("beat_id")
                    if isinstance(b, dict)
                    else getattr(b, "beat_id", "")
                )
                or ""
            )
        }
        report = await call(
            ScriptChapterValidation,
            validation_system_contract(),
            {
                "prose": take["content"],
                "selected_script": packet.get("script"),
                "must_land_beats": packet.get("must_land_beats") or [],
                "acceptance_items": (
                    sp.get("acceptance_items")
                    or packet.get("acceptance_items")
                    or []
                ),
                "rejected": sp.get("rejected") or {},
                "canon": run.generation_context.get("canon", []),
                "scene_hard_fails": sp.get("scene_hard_fails") or [],
                "scene_layout": _layout,
                "beat_id_to_scene": _beat_map,
                "scene_plan": {
                    "cards": [
                        {
                            "scene_id": (c or {}).get("scene_id")
                            if isinstance(c, dict)
                            else getattr(c, "scene_id", ""),
                            "purpose": (c or {}).get("purpose")
                            if isinstance(c, dict)
                            else getattr(c, "purpose", ""),
                            "beat_ids": [
                                str(
                                    (
                                        b.get("beat_id")
                                        if isinstance(b, dict)
                                        else getattr(b, "beat_id", "")
                                    )
                                    or ""
                                )
                                for b in (
                                    (
                                        c.get("beats")
                                        if isinstance(c, dict)
                                        else getattr(c, "beats", []) or []
                                    )
                                    or []
                                )
                                if str(
                                    (
                                        b.get("beat_id")
                                        if isinstance(b, dict)
                                        else getattr(b, "beat_id", "")
                                    )
                                    or ""
                                )
                            ],
                        }
                        for c in _scene_cards
                    ]
                },
            },
        )
        # 场记问题与章节核验问题：script 整章修订；script_scene 不整章假返工
        scene_fails = list(sp.get("scene_hard_fails") or [])
        _acc_for_validate = [
            i
            for i in (
                sp.get("acceptance_items")
                or packet.get("acceptance_items")
                or []
            )
            if isinstance(i, dict)
        ]
        from regent.novel.domain.script_protocol import AcceptanceItem

        _acc_models = [
            AcceptanceItem.model_validate(i) for i in _acc_for_validate if i
        ]
        # 契约化核验：按 item 去重 + kind 过滤，禁止复合终点多条误拦
        chapter_fails = normalize_chapter_validation_fails(
            list(report.hard_fails), _acc_models
        )
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
        # 分区：结构门（pov/banned/title/first_person）硬停；文字门（redundant/power_surface）递延到 REVIEW
        _DEFERRABLE_PROSE_FRONT = ("[front:redundant]", "[front:power_surface]")
        structural_front = [
            f for f in front_fails if not str(f).startswith(_DEFERRABLE_PROSE_FRONT)
        ]
        deferred_front = [
            f for f in front_fails if str(f).startswith(_DEFERRABLE_PROSE_FRONT)
        ]
        if structural_front:
            chapter_fails = list(dict.fromkeys([*chapter_fails, *structural_front]))
        if deferred_front:
            _append_gate_debt(
                sp,
                scene_id="__chapter__",
                tags=deferred_front,
                kind="prose_front",
                from_phase="VALIDATE",
            )
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
                # 顾问字面可 soft；power_surface/redundant 已递延到 REVIEW，不到此路径
                softable = [
                    x
                    for x in chapter_fails
                    if str(x).startswith("[commons:")
                ]
                if softable and len(softable) == len(chapter_fails):
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
                        # 修订轨迹：每轮问题/定位/正文指纹，供根因复盘
                        from regent.novel.domain.repair_locate import (
                            content_hash as _chash_full,
                        )

                        sp.setdefault("repair_history", []).append(
                            {
                                "round": repairs + 1,
                                "fails": list(other or chapter_fails),
                                "acceptance_items": _acc_for_validate,
                                "located_issues": list(
                                    (take.get("validation") or {}).get("located_issues")
                                    or report.located_issues
                                    or []
                                ),
                                "content_hash_before": _chash_full(chapter_now),
                            }
                        )
                        # 定位到真实失败场；无法定位则停机，禁止默认修末场
                        _begin_located_scene_repair(
                            production,
                            sp,
                            fails=list(other or chapter_fails),
                            instruction_prefix="章节核验未过，请改本场：",
                            take=take,
                            model_located=list(report.located_issues or []),
                        )
                        # 记录确定性定位完成后的实际结果。不能只记录模型空数组。
                        sp["repair_history"][-1]["located_issues"] = list(
                            sp.get("located_issues") or []
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
                    "scene_exit_states",
                    "scene_entry_summaries",
                    "pending_repair_ticket",
                    "chapter_dedup_quotes",
                    "located_issues",
                    "acceptance_items",
                    "repair_history",
                    "compile_soft_issues",
                    "gate_debts",
                    "audit_deferred_cont",
                    "scene_soft_fails",
                    "audit_soft_cont",
                    "validate_soft_commons",
                ):
                    sp.pop(k, None)
                sp["scene_index"] = 0
                # 重生轮从本章入口重开，不继承失败草稿的出口。
                entry = production.get("chapter_entry_state")
                confirmed = (
                    deepcopy(entry)
                    if isinstance(entry, dict)
                    else deepcopy(
                        production.get("working_state")
                        or run.generation_context.get("actual_state")
                        or {}
                    )
                )
                sp["working_state"] = confirmed
                # 重生轮台账也回退到本章入口（前章 handoff 或空台账）。
                prev_ledger = None
                for prev in reversed(run.generation_context.get("recent_chapters") or []):
                    if isinstance(prev, dict) and isinstance(prev.get("continuity_ledger"), dict):
                        prev_ledger = prev["continuity_ledger"]
                        break
                sp["continuity_ledger"] = (
                    deepcopy(prev_ledger) if prev_ledger else empty_ledger()
                )
                sp["continuity_ledger_trail"] = [deepcopy(sp["continuity_ledger"])]
                sp.pop("ledger_violations", None)
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
            if sp.get("continuity_ledger") is not None:
                production["continuity_ledger"] = deepcopy(sp["continuity_ledger"])
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
