"""多剧本择优协议：实验与生产对照臂共用的领域模型与装配。

草稿候选不得写入正式世界状态；仅选定稿可进入制作包与后续正典提交。
导演可从落选稿抽取「可借鉴点子」，但不得把落选结局拼进正片。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PROTOCOL_SCRIPT = "script"
PROTOCOL_SCRIPT_SCENE = "script_scene"

# 候选槽位只是编号，不绑定固定剧情策略；具体选题由导演 BRIEF 布置。
# 多编剧互不读稿、可并发 → 走 Hive（WRITE_SCRIPTS 一批打完）。
SCRIPT_CANDIDATE_SLOTS: tuple[str, ...] = ("alpha", "beta", "gamma", "delta")

SCRIPT_PHASES = (
    "BRIEF",
    "WRITE_SCRIPTS",
    "SELECT",
    "ASSEMBLE",
    "WRITE_CHAPTER",
    "VALIDATE",
    "ACCEPT_CHAPTER",
    "DONE",
)

SCRIPT_SCENE_PHASES = (
    "BRIEF",
    "WRITE_SCRIPTS",
    "SELECT",
    "ASSEMBLE",
    "COMPILE_SCENES",
    "WRITE_SCENE",
    "AUDIT_SCENE",
    "ASSEMBLE_CHAPTER",
    "VALIDATE",
    "ACCEPT_CHAPTER",
    "DONE",
)
SCRIPT_DIVERSITY_CONTRACT = (
    "多份候选必须在行动路径与代价类型上两两可区分，禁止同梗换皮（只改名字/场所）。"
    "导演任务只是宽松选题；具体代价、转折、打脸步骤由本候选发明。"
    "代价必须在该题材常理下可信，禁止用无杀伤力的假痛点凑戏。"
    "若已有跨章账本，优先沿用既有规则下的选择与后果，不要每章发明全新机制。"
    "若候选整体同套路，或代价明显不成立，导演应 reject_both。"
)

WEB_NOVEL_POWER_CONTRACT = (
    "若这部作品有超出日常的能力或机制，开场让读者听懂：能做什么、边界在哪、用一次要付什么。"
    "前提里没有，就不要加。禁止只甩一个名字不解释。"
    "代价必须在这部作品自己的常理下成立，禁止用几乎无后果的事凑戏。"
)

DIRECTOR_ASSIGN_CONTRACT = (
    "你是总导演，只布置编剧选题，不写剧本、不写正文。"
    "根据本章目标与方向标签，给编剧布置 4 条选题任务（slot=alpha/beta/gamma/delta）。"
    "每条任务必须简洁（一两句）、宽松：只点明发挥方向或读者要感到什么；"
    "禁止写死情节步骤、台词、具体代价类型、打脸清单、机位表、金手指细则。"
    "四条必须是彼此不同的戏剧方向：冲突切角、压力来源或不可两全的选择要可区分；"
    "禁止同梗换皮（只换场所/名字）；至少要有一条把主角逼进『选了就疼』的张力。"
    "goal_restated 用一句话重述本章要服务的读者目标即可。"
)

DIRECTOR_SELECT_CONTRACT = (
    "你是总导演，对章节阅读质量负责。你不写剧本、不写正文。"
    "面前有多份匿名候选（甲乙丙丁）：选出一本最可拍的主剧本。"
    "比较维度：戏剧张力、人物选择可信、金手指兑现清楚、钩子深、代价符合题材常理、"
    "是否回应了你布置的选题。"
    "优先选『读者会担心后果』的稿；只有气氛没有不可两全选择的稿判弱。"
    "方向标签不是大纲：若候选只会复述标签、没有自己的戏核与机制设计，应判弱。"
    "可 selected_id=reject_both 全部退回；多稿同套路必须 reject_both。"
    "允许从落选稿抽取灵感：inspiration_from 与 borrowable_ideas（短点子）；"
    "禁止把落选稿结局/关键因果拼进选定稿。"
    "禁止给候选附带自夸分。"
    "must_land_beats 只写必须拍出的戏效果/读者感受（短句），"
    "禁止写金手指操作细则、禁止写「缓存/调用/机制」术语、禁止锁死具体人名节目名作为不可改正文。"
    "acceptance_items：把选定剧本的章末义务拆成可独立核验的条目（3–8 条），每条只含一个义务；"
    "kind 三选一：state_change=本章结束时正文必须成立的既成事实；"
    "cognition=主角视角内必须出现的认知变化；"
    "dilemma=本章只需建立/逼近的未解困境或悬念，不要求本章拍板解决。"
    "禁止把『面临选择』和『完成选择』写成同一条；禁止把多个境界/持有/认知塞进一条。"
    "min_evidence 写最小可观察证据方向（非关键词清单）。"
    "allow_writer_room 只约束执笔风格与感官，禁止写「不得改动金手指规则/代价类型」。"
    "输出选中ID、短理由、最危险弱点、must_land_beats、acceptance_items、allow_writer_room、可借鉴点子。"
)


class CastDraftPersona(BaseModel):
    name: str = ""
    desire: str = ""
    relation: str = ""
    knowledge: str = ""
    limit: str = ""
    voice: str = ""


class ScriptWriterTask(BaseModel):
    """导演给编剧的一条宽松选题（不是大纲）。"""

    slot: str = Field(description="alpha/beta/gamma/delta")
    task: str = Field(min_length=4, max_length=80, description="一两句宽松选题")


class ScriptAssignmentBoard(BaseModel):
    """导演布置的编剧任务板。"""

    goal_restated: str = Field(default="", description="一句话重述本章读者目标")
    tasks: list[ScriptWriterTask] = Field(min_length=3, max_length=4)


def normalize_assignment_board(
    board: ScriptAssignmentBoard,
    *,
    slots: tuple[str, ...] = SCRIPT_CANDIDATE_SLOTS,
) -> dict[str, str]:
    """把导演任务对齐到固定槽位；缺槽则用宽松占位，不发明剧情。"""
    by_slot: dict[str, str] = {}
    for item in board.tasks:
        sid = str(item.slot or "").strip().lower()
        task = str(item.task or "").strip()
        if sid in slots and task and sid not in by_slot:
            by_slot[sid] = task[:80]
    for sid in slots:
        if sid not in by_slot:
            by_slot[sid] = "围绕本章目标写一条可拍、与其它候选可区分的因果路线；自行发明戏核与代价。"
    return by_slot


def task_for_slot(assignment: dict[str, str] | None, slot: str) -> str:
    """读取导演布置给某槽位的选题。"""
    board = assignment or {}
    task = str(board.get(slot) or "").strip()
    if task:
        return task
    return "围绕本章目标写一条可拍、与其它候选可区分的因果路线；自行发明戏核与代价。"


class ChapterScript(BaseModel):
    """紧凑可演剧本（草稿）。不得直接写入正式世界状态。"""

    title_line: str = ""
    start_state: str = ""
    end_change: str = ""
    protagonist_want: str = ""
    opposition: str = ""
    beats: list[str] = Field(default_factory=list)
    turning_point: str = ""
    turning_reason: str = ""
    cost: str = ""
    cost_type: str = Field(
        default="",
        description="代价类型标签，用于候选差异化（如暴露/亲情/血债/把柄）",
    )
    key_dialogue: list[str] = Field(default_factory=list)
    protagonist_knows: str = ""
    protagonist_weighs: str = ""
    reading_question: str = ""
    power_payoff: str = Field(
        default="",
        description="金手指如何在本章兑现信息差/能力差",
    )
    power_mechanism: str = Field(
        default="",
        description="本候选确立的金手指如何运作（选中后成为跨章正史，须自洽）",
    )
    power_limits: str = Field(
        default="",
        description="本候选确立的金手指边界与禁止项（选中后成为跨章正史）",
    )
    cast_draft: list[CastDraftPersona] = Field(default_factory=list)


class AcceptanceItem(BaseModel):
    """章末验收义务：一条只含一个可独立核验的义务。

    kind 决定核验口径，避免把困境/认知/既成结果混成同一硬失败：
    - state_change：本章结束时正文必须成立的事实
    - cognition：主角视角内必须出现的认知变化
    - dilemma：只需建立/逼近的未解困境，不要求本章解决
    - display：表现层效果，缺失可软化，不单独构成生产硬停
    """

    item_id: str = Field(min_length=1)
    kind: Literal["state_change", "cognition", "dilemma", "display"] = "state_change"
    text: str = Field(min_length=1, description="单一义务，禁止复合句塞多项")
    min_evidence: str = Field(default="", description="最小可观察证据方向")
    required: bool = True
    scene_ids: list[str] = Field(
        default_factory=list, description="责任场次；COMPILE 阶段填写"
    )


class ScriptChoice(BaseModel):
    """导演择优结果。selected_id 可为 alpha/beta/gamma/delta 或 reject_both。"""

    selected_id: str = "reject_both"
    reason: str = ""
    weakness: str = ""
    must_land_beats: list[str] = Field(default_factory=list)
    acceptance_items: list[AcceptanceItem] = Field(
        default_factory=list,
        description="选定剧本的章末义务清单；空则由 end_change/must_land 推导",
    )
    allow_writer_room: str = ""
    direction_notes: str = ""
    # 落选稿灵感：只作提示，不得把落选结局拼进正片
    inspiration_from: list[str] = Field(default_factory=list)
    borrowable_ideas: list[str] = Field(default_factory=list)

    @field_validator("selected_id")
    @classmethod
    def _normalize_selected_id(cls, value: str) -> str:
        sid = str(value or "").strip().lower()
        if sid in {"reject_all", "reject", "none", ""}:
            return "reject_both"
        return sid


_DILEMMA_MARKERS = (
    "决定是否",
    "是否信任",
    "是否交出",
    "是否说出",
    "面临选择",
    "必须选择",
    "必须决定",
    "悬而未决",
    "留下悬念",
    "两难",
)
_COGNITION_MARKERS = (
    "意识到",
    "认识到",
    "明白",
    "察觉到自己",
    "知道自己的",
    "发现自己已经",
    "惊觉",
)
_STATE_MARKERS = (
    "成为",
    "转变为",
    "持有",
    "获得",
    "得到",
    "进入",
    "突破",
    "变为",
    "转入",
    "已经学会",
    "已经能够",
    "最终选择",
    "已经决定",
    "选择了",
    "做出决定",
    "说出",
    "交出",
)


def classify_acceptance_kind(text: str) -> Literal["state_change", "cognition", "dilemma", "display"]:
    """按义务文案推断验收类型；复合句优先命中更具体的类型。"""
    t = str(text or "")
    if any(m in t for m in _DILEMMA_MARKERS):
        # 「必须决定是否X」本身是困境；「决定并完成X」才是结果——
        # 未出现完成态动词时按 dilemma，避免要求本章拍板。
        if any(m in t for m in ("已经决定", "最终选择", "完成了选择", "做出了决定")):
            return "state_change"
        return "dilemma"
    if any(m in t for m in _COGNITION_MARKERS):
        return "cognition"
    if any(m in t for m in _STATE_MARKERS):
        return "state_change"
    return "display"


def split_obligation_clauses(text: str) -> list[str]:
    """把复合义务句拆成单一义务分句。"""
    import re

    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(
        r"[；;。]|——|以及|并且|同时|此外|另外|，(?=[^，]{6,})|、(?=[^，]{4,})",
        raw,
    )
    out: list[str] = []
    for p in parts:
        s = str(p or "").strip(" ，,；;。")
        if len(s) < 4:
            continue
        out.append(s[:120])
    return out or ([raw[:120]] if len(raw) >= 4 else [])


def derive_acceptance_items(
    script: ChapterScript | dict[str, Any] | None,
    choice: ScriptChoice | dict[str, Any] | None,
) -> list[AcceptanceItem]:
    """得到本章验收清单：优先用导演结构化输出，否则从 end_change/must_land 推导。"""
    if isinstance(choice, ScriptChoice):
        provided = list(choice.acceptance_items or [])
        must_land = list(choice.must_land_beats or [])
        choice_data = choice
    else:
        cdict = dict(choice or {})
        provided = [
            AcceptanceItem.model_validate(x)
            for x in (cdict.get("acceptance_items") or [])
            if x
        ]
        must_land = [str(x) for x in (cdict.get("must_land_beats") or []) if str(x).strip()]
        choice_data = None

    if provided:
        items: list[AcceptanceItem] = []
        seen: set[str] = set()
        for i, a in enumerate(provided):
            iid = str(a.item_id or "").strip() or f"acc{i + 1}"
            if iid in seen:
                iid = f"{iid}_{i + 1}"
            seen.add(iid)
            items.append(
                a.model_copy(
                    update={
                        "item_id": iid,
                        "text": str(a.text or "").strip()[:160],
                        "required": bool(a.required)
                        if a.kind != "display"
                        else False,
                    }
                )
            )
        # must_land 未覆盖时补 display 项，不重复已有 text
        existing_text = {i.text for i in items}
        for j, beat in enumerate(must_land):
            bt = beat.strip()
            if not bt or any(bt in e or e in bt for e in existing_text):
                continue
            items.append(
                AcceptanceItem(
                    item_id=f"ml{j + 1}",
                    kind="display",
                    text=bt[:120],
                    required=False,
                )
            )
        return items

    if isinstance(script, ChapterScript):
        end = str(script.end_change or "")
    else:
        end = str((script or {}).get("end_change") or "")

    items = []
    for i, clause in enumerate(split_obligation_clauses(end)):
        kind = classify_acceptance_kind(clause)
        items.append(
            AcceptanceItem(
                item_id=f"end{i + 1}",
                kind=kind,
                text=clause,
                required=kind != "display",
                min_evidence="",
            )
        )
    existing = {i.text for i in items}
    for j, beat in enumerate(must_land):
        bt = beat.strip()
        if not bt or any(bt in e or e in bt for e in existing):
            continue
        items.append(
            AcceptanceItem(
                item_id=f"ml{j + 1}",
                kind="display",
                text=bt[:120],
                required=False,
            )
        )
    return items


class ChapterCreativeAccept(BaseModel):
    """导演整章创作验收（非事实核验）。"""

    accept: bool = False
    # supply=候选无好戏；pick=有好戏却选错；render=稿好文差；ok=通过
    fault: Literal["supply", "pick", "render", "ok"] = "supply"
    notes: str = ""


def empty_script_state() -> dict[str, Any]:
    return {
        "candidates": {},
        "rejected": {},
        "selected_id": "",
        "choice": None,
        "assignment": {},
        "assignment_goal": "",
        "production_packet": None,
        "fault_taxonomy": {"supply": None, "pick": None, "render": None},
        "creative_repairs": 0,
        "hive": None,
    }


def route_script_hive(
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """编剧 Hive 路由：互不读稿且槽位≥2 才并发。

    与角色节拍 Hive 同构：隔离不成立则不得并发（退回串行由调用方处理）。
    本函数只做决策与证据，不发起调用。
    """
    slots = tuple(payloads)
    if len(payloads) < 2:
        return {
            "enabled": False,
            "reason": "single_writer" if payloads else "empty_writers",
            "slots": list(slots),
            "leaks": [],
            "fingerprints": {},
        }
    leaks: list[str] = []
    fingerprints: dict[str, str] = {}
    for slot, payload in payloads.items():
        for banned in ("other_scripts", "peer_drafts", "candidates"):
            if payload.get(banned):
                leaks.append(f"{slot} 携带了禁止字段 {banned}")
        for other in slots:
            if other == slot:
                continue
            if payload.get(f"script_{other}") is not None:
                leaks.append(f"{slot} 的上下文含 script_{other}")
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        fingerprints[slot] = hashlib.sha256(blob.encode()).hexdigest()[:16]
    if leaks:
        return {
            "enabled": False,
            "reason": "isolation_violated",
            "slots": list(slots),
            "leaks": leaks,
            "fingerprints": fingerprints,
        }
    if len(set(fingerprints.values())) != len(fingerprints):
        return {
            "enabled": False,
            "reason": "identical_contexts",
            "slots": list(slots),
            "leaks": [],
            "fingerprints": fingerprints,
        }
    return {
        "enabled": True,
        "reason": "isolated_and_concurrent",
        "slots": list(slots),
        "leaks": [],
        "fingerprints": fingerprints,
    }


def assemble_production_packet(
    *,
    selected: ChapterScript,
    choice: ScriptChoice,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """程序装配：只使用选定剧本；弃选内容不得进入此包。

    导演的 borrowable_ideas 可进入 direction，供执笔参考，但不引入落选剧本正文。
    """
    cast = [
        {
            "name": p.name,
            "desire": p.desire,
            "relation": p.relation,
            "knowledge": p.knowledge,
            "limit": p.limit,
            "voice": p.voice,
        }
        for p in selected.cast_draft
    ]
    direction = choice.model_dump(mode="json")
    # 把可借鉴点子并入执笔余地，避免丢失
    ideas = [str(x).strip() for x in (choice.borrowable_ideas or []) if str(x).strip()]
    if ideas:
        room = str(choice.allow_writer_room or "").strip()
        spark = "可借鉴（不得改选定因果终点）：" + "；".join(ideas[:6])
        direction["allow_writer_room"] = f"{room}｜{spark}" if room else spark
    packet = {
        "status": "draft_selected",
        "script": selected.model_dump(mode="json"),
        "direction": direction,
        "cast": cast,
        "must_land_beats": list(choice.must_land_beats),
        "acceptance_items": [
            i.model_dump(mode="json") for i in derive_acceptance_items(selected, choice)
        ],
        "reading_question": selected.reading_question,
        "power_payoff": selected.power_payoff,
        "isolation_note": "弃选剧本与其 cast_draft 不得写入本包或正式事实；仅允许短灵感提示。",
    }
    if meta:
        packet["meta"] = dict(meta)
    return packet


def assert_rejected_isolated(
    packet: dict[str, Any],
    rejected: dict[str, Any],
    *,
    selected: dict[str, Any] | ChapterScript | None = None,
) -> None:
    """若弃选**独特**内容泄漏进制作包则抛错。

    两候选常共用书名式 title_line，不能仅凭标题相等判泄漏；只检查相对选定稿
    可区分、且足够长的字段（代价类型、兑现路径、结尾变化等）。

    导演的 inspiration_from / borrowable_ideas 允许点名落选点子，故不扫 direction。
    """
    # 只扫会进入正片制作的部分；灵感提示不计入泄漏。
    check = {
        "script": packet.get("script"),
        "cast": packet.get("cast"),
        "must_land_beats": packet.get("must_land_beats"),
        "reading_question": packet.get("reading_question"),
        "power_payoff": packet.get("power_payoff"),
    }
    blob = json.dumps(check, ensure_ascii=False)
    selected_blob = ""
    if selected is not None:
        if isinstance(selected, ChapterScript):
            selected_blob = json.dumps(selected.model_dump(mode="json"), ensure_ascii=False)
        else:
            selected_blob = json.dumps(selected, ensure_ascii=False)
    fingerprint_fields = (
        "cost_type",
        "power_payoff",
        "end_change",
        "turning_point",
        "cost",
        "protagonist_weighs",
        "reading_question",
    )
    for key, script in rejected.items():
        if isinstance(script, ChapterScript):
            data = script.model_dump(mode="json")
        elif isinstance(script, dict):
            data = script
        else:
            continue
        for field in fingerprint_fields:
            val = str(data.get(field) or "").strip()
            if len(val) < 4:
                continue
            if selected_blob and val in selected_blob:
                continue
            if val in blob:
                raise ValueError(
                    f"rejected script leaked into packet: {key or field}"
                )


def scripts_look_same_trope(a: ChapterScript, b: ChapterScript) -> bool:
    """启发式：代价类型与转折同质则视为同梗（供导演/测试参考）。"""
    ca = (a.cost_type or a.cost or "").strip()
    cb = (b.cost_type or b.cost or "").strip()
    ta = (a.turning_point or "").strip()
    tb = (b.turning_point or "").strip()
    if ca and cb and ca == cb and ta and tb and ta == tb:
        return True
    return False


def assign_acceptance_to_scenes(
    items: list[AcceptanceItem],
    plan_cards: list[Any],
) -> list[AcceptanceItem]:
    """把验收义务映射到场景卡；覆盖 purpose/exit/beats 文本。"""
    from regent.novel.domain.scene_card import _text_covers

    # 显式映射优先，避免改写后的义务被字面匹配误判缺失。
    explicit: dict[str, list[str]] = {}
    for card in plan_cards:
        data = card if isinstance(card, dict) else card.model_dump()
        for item_id in data.get("acceptance_ids") or []:
            explicit.setdefault(str(item_id), []).append(str(data.get("scene_id") or ""))
    out: list[AcceptanceItem] = []
    for item in items:
        if item.item_id in explicit:
            owners = list(dict.fromkeys(explicit[item.item_id]))
            out.append(item.model_copy(update={"scene_ids": owners}))
            continue
        sids: list[str] = []
        for card in plan_cards:
            if isinstance(card, dict):
                sid = str(card.get("scene_id") or "")
                corpus = [
                    str(card.get("purpose") or ""),
                    str(card.get("exit_change") or ""),
                    str(card.get("core_choice") or ""),
                ]
                for b in card.get("beats") or []:
                    corpus.append(
                        str(
                            (b.get("text") if isinstance(b, dict) else getattr(b, "text", ""))
                            or ""
                        )
                    )
            else:
                sid = str(getattr(card, "scene_id", "") or "")
                corpus = [
                    str(getattr(card, "purpose", "") or ""),
                    str(getattr(card, "exit_change", "") or ""),
                    str(getattr(card, "core_choice", "") or ""),
                ]
                for b in getattr(card, "beats", []) or []:
                    corpus.append(str(getattr(b, "text", "") or ""))
            if not sid:
                continue
            if _text_covers(item.text, corpus, min_frag=6) or any(
                item.text[:8] and item.text[:8] in c for c in corpus if c
            ):
                sids.append(sid)
        out.append(item.model_copy(update={"scene_ids": sids}))
    return out


def ensure_acceptance_assigned(
    items: list[AcceptanceItem],
    plan_cards: list[Any],
) -> tuple[list[AcceptanceItem], list[Any]]:
    """映射责任场但不改写计划。缺失项交给导演重新分场。

    自动把未匹配义务追加到末场，会把前场已发生的动作重演一遍，
    也可能把白天事件塞到夜间收束之后；文本相似度不具有编剧权限。
    """
    return assign_acceptance_to_scenes(items, plan_cards), list(plan_cards)


def acceptance_plan_issues(items: list[AcceptanceItem]) -> list[str]:
    """required 义务未落到任何责任场次 → 计划期硬问题，不得拖到整章写完。"""
    issues: list[str] = []
    for item in items:
        if not item.required:
            continue
        if len(item.scene_ids) > 1:
            issues.append(f"acceptance_multiple_owners:{item.item_id}:{','.join(item.scene_ids)}")
        if item.scene_ids:
            continue
        issues.append(f"acceptance_unassigned:{item.item_id}:{item.text[:24]}")
    return issues


def match_fail_to_acceptance(
    fail: str,
    items: list[AcceptanceItem],
) -> AcceptanceItem | None:
    """把核验 hard_fail 文案匹配到验收义务（用于去重与 kind 过滤）。"""
    msg = str(fail or "")
    if not msg:
        return None
    # 显式 [acc:item_id]
    import re

    m = re.search(r"\[acc:([^\]]+)\]", msg)
    if m:
        iid = m.group(1).strip()
        for item in items:
            if item.item_id == iid:
                return item
    folded = re.sub(r"[\s，。！？、；：\"\"''「」『』（）()\[\]…·.,!?;:'\"\-—_]+", "", msg)
    best: AcceptanceItem | None = None
    best_score = 0
    for item in items:
        key = re.sub(
            r"[\s，。！？、；：\"\"''「」『』（）()\[\]…·.,!?;:'\"\-—_]+",
            "",
            item.text,
        )
        if not key:
            continue
        score = 0
        if key in folded:
            score = len(key)
        else:
            window = min(10, len(key))
            if window >= 6:
                for i in range(0, len(key) - window + 1):
                    if key[i : i + window] in folded:
                        score = max(score, window)
                        break
        if score > best_score:
            best_score = score
            best = item
    return best if best_score >= 6 else None


def normalize_chapter_validation_fails(
    fails: list[str],
    acceptance_items: list[AcceptanceItem] | None = None,
) -> list[str]:
    """按验收契约整理 hard_fails：去重、按 kind 过滤误拦。

    根治规则（xuanhuan 失败）：
    1. 同一 acceptance item 只保留一条，避免一次缺失产生多条修订任务；
    2. dilemma 义务不要求本章「做出/完成决定」——只拦「完全未建立困境」；
    3. display 不单独构成生产硬停；
    4. 无法匹配到任何义务、且不像程序门禁标签的文案仍保留（不吞真实问题）。
    """
    items = list(acceptance_items or [])
    if not fails:
        return []
    if not items:
        # 无契约时仅做字面去重
        return list(dict.fromkeys(str(f).strip() for f in fails if str(f).strip()))

    kept_by_item: dict[str, str] = {}
    unmatched: list[str] = []
    dilemma_demand = re.compile(
        r"未(做出|完成|明确|进行).{0,8}(决定|选择|表态)|没有(做出|完成).{0,6}(决定|选择)"
    )
    established_missing = re.compile(
        r"未(建立|出现|呈现|提及|涉及|留下)|完全没有|毫无|缺少.*(困境|悬念|选择|张力)"
    )

    for raw in fails:
        msg = str(raw or "").strip()
        if not msg:
            continue
        # 程序门禁 / front_gate 标签原样保留；[acc:…] 是验收项，走契约路径
        if not msg.startswith("[acc:") and (
            msg.startswith(("[commons:", "[front:", "[pov:", "[soft-cont:"))
            or msg.startswith(("front:", "pov:"))
        ):
            unmatched.append(msg)
            continue
        item = match_fail_to_acceptance(msg, items)
        if item is None:
            unmatched.append(msg)
            continue
        if item.kind == "display":
            continue  # 表现层不单独硬停
        if item.kind == "dilemma":
            # 要求本章拍板 → 误拦；困境完全未建立 → 保留
            if dilemma_demand.search(msg) and not established_missing.search(msg):
                continue
        prev = kept_by_item.get(item.item_id)
        if prev is None or len(msg) > len(prev):
            kept_by_item[item.item_id] = msg

    out: list[str] = list(kept_by_item.values())
    out.extend(dict.fromkeys(unmatched))
    return out


def validation_system_contract() -> str:
    """VALIDATE 的统一验收协议：写作与核验共用同一清单与口径。"""
    return (
        "核验正文是否兑现选定剧本与 acceptance_items（章末义务清单）。"
        "按 item.kind 口径判定，禁止自由加码：\n"
        "- state_change：章末既成事实是否成立；接受语义等价兑现，"
        "不得要求境界名/道具名等术语逐字出现，除非清单明确写了该字面。\n"
        "- cognition：主角视角内是否出现对应认知/反应；他人察觉≠主角意识到。\n"
        "- dilemma：本章是否建立/逼近该未解困境；不要求本章做出最终决定。"
        "禁止因「未拍板」记 hard_fail；只有完全未建立困境才可记。\n"
        "- display：缺失只进 soft_notes，不进 hard_fails。\n"
        "每条 hard_fails 格式：[acc:item_id] 一句话问题；摘录「原文片段」…\n"
        "同一 item_id 只报一条，禁止把复合终点拆成多条重复修订任务。"
        "若能判断场次，fill located_issues："
        "有引文时 scene_ids + evidence_quotes；缺失节拍无引文时填 beat_ids"
        "（取自 payload.beat_id_to_scene）。场次对照 payload.scene_layout，禁止猜分场。"
        "抽取有逐字 quote 的事实；你不负责戏剧决策，也不改写正文。"
    )
