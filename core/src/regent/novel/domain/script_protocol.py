"""多剧本择优协议：实验与生产对照臂共用的领域模型与装配。

草稿候选不得写入正式世界状态；仅选定稿可进入制作包与后续正典提交。
导演可从落选稿抽取「可借鉴点子」，但不得把落选结局拼进正片。
"""

from __future__ import annotations

import hashlib
import json
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
    "目标产品是付费网文。主角必须有明确金手指，"
    "开场前半段让读者听懂：能做什么、不能做什么、用一次付什么代价。"
    "禁止只甩能力名不解释；禁止平凡无外挂开局；禁止金手指只旁白从不兑现。"
    "代价由剧本候选设计并接受导演检验：必须真疼、可兑现、符合场域常理；"
    "禁止用「圈子里人人默认、几乎无后果」的事当主代价。"
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
    "allow_writer_room 只约束执笔风格与感官，禁止写「不得改动金手指规则/代价类型」。"
    "输出选中ID、短理由、最危险弱点、must_land_beats、allow_writer_room、可借鉴点子。"
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


class ScriptChoice(BaseModel):
    """导演择优结果。selected_id 可为 alpha/beta/gamma/delta 或 reject_both。"""

    selected_id: str = "reject_both"
    reason: str = ""
    weakness: str = ""
    must_land_beats: list[str] = Field(default_factory=list)
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
