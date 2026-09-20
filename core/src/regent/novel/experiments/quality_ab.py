"""质量对照实验 A/B/C：脚本协议，不注册到 KNOWN_EXECUTORS。

A：设定+目标 → 直接写作 → 一次核验
B：场景规划 → 事件结算 → 执笔 → 复看 → 核验（镜像生产导演链，离线跑）
C：动机/冲突/边界 → 执笔 → 抽取草稿事实 → 核验通过才视为可提交

默认不调付费模型；``SimProvider`` 用于干跑贯通，``--live`` 才接真实 provider。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Sequence

from pydantic import BaseModel, Field

from regent.model import ModelProvider
from regent.model.provider import ModelUsage, StructuredModelResponse
from regent.novel.domain.script_protocol import (
    DIRECTOR_ASSIGN_CONTRACT,
    DIRECTOR_SELECT_CONTRACT,
    PROTOCOL_SCRIPT,
    SCRIPT_DIVERSITY_CONTRACT,
    WEB_NOVEL_POWER_CONTRACT,
    CastDraftPersona,
    ChapterCreativeAccept,
    ChapterScript,
    ScriptAssignmentBoard,
    ScriptChoice,
    ScriptWriterTask,
    assemble_production_packet,
    assert_rejected_isolated,
    normalize_assignment_board,
    task_for_slot,
)

# 实验包旧名兼容
ChapterAccept = ChapterCreativeAccept

CALL_CAP_PER_SAMPLE = 40
MAX_REVISIONS = 1
# 网文首章：过长硬门会逼模型注水。人工四/五轮已确认「硬凑字数→开头水→不想看」。
# 保留下限防提纲体；上限提示防注水（超长不算硬失败，注水才算）。
MIN_PROSE_CHARS = 1200
TARGET_PROSE_CHARS = 1800
MAX_DENSE_CHARS = 2800
# 实验用粗略计价：每 1k token ≈ 1 minor；仅作对照，非账单。
COST_MINOR_PER_1K_TOKENS = 1

# 第一章开场契约：正文必须作为小说第一章独立成立，不得写成半截中段。
OPENING_CONTRACT = (
    "本篇必须作为网络小说《第一章》开场独立成立：读者此前一无所知。"
    "开篇三段内须让人知道：谁、在哪、这场要面对什么。"
    "金手指必须讲清楚：能做什么、不能做什么、用一次付什么代价；"
    "用主角此刻感知交代，禁止黑话堆砌、禁止只甩能力名不解释规则。"
    "禁止跳入未解释的任务黑话、未介绍的同伴暗号、半截行动。"
    "人物动机与经济状况必须自洽，禁止自打脸。"
    "主体清晰：整场只服务一个核心戏剧问题，不旁生第二套主线。"
    "同一场内时空连续：不无故跳地点/跳时间，因果要让小白读者跟得上。"
)

# 冲突尺度：可读性门禁，不是剧情种子（剧情代价由编剧候选发明、导演择优）。
SCALE_CONTRACT = (
    "冲突规模必须匹配当场局面，禁止小题大做与虚高灾难。"
    "代价必须让该场域普通人/从业者觉得「这确实疼」；"
    "禁止把圈子里默认存在、几乎无杀伤力的事（如恋综有剧本）写成反复权衡的核心恐惧。"
)

# 网文产品契约：见 domain.script_protocol.WEB_NOVEL_POWER_CONTRACT（实验/生产共用）。

# 网文钩子：吸引力要有，但不能靠虚高与跳跃。
WEB_HOOK_CONTRACT = (
    "目标读者是网文用户，不是慢热传统文学。"
    "第1段就进入现场，禁止无冲突日常铺垫超过两段。"
    "前800字内须让读者听懂处境与金手指规则；钩子要具体可感知，不要虚高口号。"
    "章末留未解压力即可；禁止说教总结句。"
)

# 代入感：人工判定「没有阅读动力」的根因——像旁观讲故事，不像钻进主角脑子。
IMMERSION_CONTRACT = (
    "网文第一章的第一任务不是讲完一个故事，而是让读者强烈代入主角，"
    "愿意设身处地从主角角度考虑「我该怎么办」。"
    "必须锁定单一主角视角（有限知情），禁止上帝旁观轮流介绍多人心理。"
    "用主角此刻的欲望、恐惧、难堪、算计驱动叙事；读者应能感到「这是我的麻烦，也是我的外挂」。"
    "开场就建立身体感觉与利害关系（冷、痛、欠、怕丢人、怕失去、怕金手指暴露），不要先做全知说明。"
    "关键选择出现时，先写主角内心权衡的具体选项与代价，再写外在动作。"
    "禁止像新闻稿/教案一样交代情节；禁止让读者站在窗外看戏。"
    "代入要强：每一段都要让读者更难抽身，而不是旁观热闹。"
)

# 反注水：硬凑字数会直接杀死开场。
DENSITY_CONTRACT = (
    "禁止为凑字数注水。"
    f"目标约{TARGET_PROSE_CHARS}字，不少于{MIN_PROSE_CHARS}字；宁短而密，勿长而水。"
    f"超过{MAX_DENSE_CHARS}字通常说明在注水，应删冗余而非继续写。"
    "每一段必须推进至少一项：利害升温、信息差、主角权衡、不可逆后果、金手指兑现。"
    "禁止同义反复、无功能环境描写、重复解释已知信息、假抒情扩写。"
    "对话禁止车轱辘；景物最多一两句服务压迫感，不得独立成段灌水。"
)

FRAGMENTS: tuple[dict[str, str], ...] = (
    {
        "id": "f1_appraisal",
        "genre": "鉴宝都市",
        "title": "真假一目",
        "slot": "chapter1_opening",
        "channel": "web_novel",
        "power": "鉴宝真假一目：接触器物可见「真伪/瑕疵/隐藏瑕点」浮层，每次使用头痛加剧，过量会短暂失明。",
        "brief": (
            "网文第一章。落魄学徒陈默在拍卖预展被当众羞辱：他指出一件「官窑」是仿品，"
            "专家与债主联手打脸。危急时刻金手指亮起，他看见内壁隐藏款识与修复缝。"
            "要用金手指当众翻盘，就会暴露能力；不用就会被毁约、欠债暴涨。"
            "读者必须立刻明白：他凭什么能赢——因为看得见别人看不见的真假。"
        ),
        "goal": (
            "代入：站在陈默位置决定「翻盘还是装瞎」。"
            "金手指必须当场兑现信息差；章末留下更大局中人盯上他。"
        ),
        "must_not": "禁止平凡修表日常；禁止无金手指的纯悬疑小品；禁止专家突然良心发现无脑送胜利。",
        "hook": "开场被当众羞辱；半程前金手指给出不可伪造的真假证据。",
        "immersion": "贴陈默的耳鸣、头痛、怕暴露、算计何时亮底牌。",
    },
    {
        "id": "f2_reborn",
        "genre": "重生逆袭",
        "title": "催债那天",
        "slot": "chapter1_opening",
        "channel": "web_novel",
        "power": "重生回催债上门当天：记得前世谁会卖她、哪份合同是坑、哪通电话能救命；但改命运会触发「校正」——亲近之人先倒霉。",
        "brief": (
            "网文第一章。苏晚重生回前夫家族上门催债、逼她签字那天。"
            "孩子在门后。她记得前世签完字后家破人亡的路线。"
            "金手指是先知：她知道哪句反驳能撕破局，也知道一改就会让儿子先发烧住院（校正）。"
            "读者要爽在「我知道陷阱」，痛在「救自己可能伤孩子」。"
        ),
        "goal": (
            "代入：替苏晚在「按旧路装可怜」和「用重生情报硬刚」之间选。"
            "重生信息必须当场打出可见优势；章末校正征兆出现。"
        ),
        "must_not": "禁止无重生的纯苦情现实文；禁止金手指只回忆不开地图炮式兑现；禁止鸡汤和解。",
        "hook": "门铃即催债；前500字内重生记忆+第一个打脸信息差。",
        "immersion": "贴苏晚的心悸、对孩子的怕、对前世仇人的恨与算计。",
    },
    {
        "id": "f3_awaken",
        "genre": "异能觉醒",
        "title": "血偿一刀",
        "slot": "chapter1_opening",
        "channel": "web_novel",
        "power": "受伤觉醒「血债可视化」：能看见谁欠谁一条命/一次背叛的血丝因果，并可强行索偿一次；索偿会反噬自身鲜血。",
        "brief": (
            "网文第一章。工地/仓运里，主角林恪失手（或被陷害）重伤发小。"
            "众人要他偿命式赔付。濒死疼痛中金手指觉醒：他看见工头身上缠着吞过数人命的血丝。"
            "他可以立刻索偿翻盘，但会大失血；也可以忍下被钉死成罪人。"
            "禁止写成无异能的生活流道歉文；读者要爽在「真相被看见」。"
        ),
        "goal": (
            "代入：受伤的林恪决定「现在索偿还是先忍」。"
            "异能必须改变力量对比；章末更大血丝主人露头。"
        ),
        "must_not": "禁止特工潜入；禁止无异能的纯失手和解；禁止觉醒后无敌无代价。",
        "hook": "开场即伤害与围攻；半程前血丝异能亮出不可辩驳的把柄。",
        "immersion": "贴林恪的痛、愧、怒、怕暴露异能、怕发小真死。",
    },
    {
        "id": "f4_ent_gender",
        "keyword_rails": "1",
        "genre": "用户自选方向",
        "title": "方向关键词",
        "slot": "chapter1_opening",
        "channel": "web_novel",
        # direction/brief/goal 由 fragment_for_run() 按用户关键词注入；此处仅占位。
        "direction": "",
        "power": "",
        "brief": "",
        "goal": "",
        "must_not": "",
        "hook": "",
        "immersion": "",
    },
)

PROTOCOLS: dict[str, dict[str, Any]] = {
    "A": {
        "name": "direct_write",
        "steps": ["assemble_goal", "write_scene", "validate_once"],
        "purpose": "简单基线：设定与目标 → 直接写作 → 一次核验",
    },
    "B": {
        "name": "director_scene_settle",
        "steps": [
            "plan_scene",
            "scene_resolve",
            "render_prose",
            "watch_prose",
            "validate",
        ],
        "purpose": "当前生产导演整场结算链路（离线镜像，不改生产默认）",
    },
    "C": {
        "name": "director_bounds_then_write",
        "steps": [
            "director_motives_and_bounds",
            "write_scene_within_bounds",
            "extract_draft_facts",
            "validate_then_commit_facts",
        ],
        "purpose": "先边界后写作，事实后置核验再进正式状态",
    },
    "S": {
        "name": "script_select_then_write",
        "steps": [
            "write_script_alpha",
            "write_script_beta",
            "write_script_gamma",
            "write_script_delta",
            "director_select",
            "assemble_packet",
            "write_chapter",
            "validate",
            "director_accept",
        ],
        "purpose": "多剧本择优→执导要求→装配→整章执笔→核验→导演验收（实验切片，非生产）",
    },
    "X": {
        "name": "scene_exec_then_write",
        "steps": [
            "write_script_alpha",
            "write_script_beta",
            "write_script_gamma",
            "write_script_delta",
            "director_select",
            "assemble_packet",
            "compile_scenes",
            "write_scene_loop",
            "supervise_scenes",
            "edit_chapter",
            "validate",
            "director_accept",
            "extract_facts_from_prose",
        ],
        "purpose": "多剧本择优→导演分场→逐场执笔+场记→整章剪辑→核验→验收→正文提取事实",
    },
}

BLIND_AXES = (
    "want_to_continue",
    "character_vivid",
    "credible_cost",
    "scene_presence",
)


class SceneProse(BaseModel):
    content: str = Field(min_length=1)

    @property
    def char_count(self) -> int:
        return len(self.content.strip())


def _prose_too_short(prose: str) -> bool:
    return len((prose or "").strip()) < MIN_PROSE_CHARS


def _finalize_result(
    *,
    fragment_id: str,
    protocol: str,
    provider: MeteredProvider,
    prose: str,
    hard_fails: list[str],
    used_revision: bool,
    stop_reason: str,
    facts: list[str],
    facts_committed: bool,
    steps: list[str],
    artifacts: dict[str, Any],
) -> SampleResult:
    """统一完成判定：硬失败、预算、过短、C 未提交事实都不得记成功。"""
    artifacts = dict(artifacts)
    chars = len((prose or "").strip())
    artifacts["prose_chars"] = chars
    artifacts["min_prose_chars"] = MIN_PROSE_CHARS
    artifacts["cache_usage"] = provider._meter.cache_report()
    reason = stop_reason
    if hard_fails and not reason:
        reason = "hard_fail"
    if _prose_too_short(prose) and not reason:
        reason = f"too_short:{chars}<{MIN_PROSE_CHARS}"
    if protocol == "C" and not facts_committed and not reason:
        reason = "facts_not_committed"
    if protocol == "S" and not facts_committed and not reason:
        reason = "not_accepted_or_uncommitted"
    completed = (
        bool(prose)
        and not hard_fails
        and not _prose_too_short(prose)
        and (facts_committed if protocol in {"C", "S"} else True)
        and not reason
    )
    if completed:
        reason = "ok"
    elif not reason:
        reason = "incomplete"
    return SampleResult(
        fragment_id=fragment_id,
        protocol=protocol,
        blind_id="",
        completed=completed,
        calls_used=provider._meter.calls,
        cost_minor=provider._meter.cost_minor,
        hard_fail_count=len(hard_fails),
        used_revision=used_revision,
        stop_reason=reason,
        prose=prose,
        facts=facts,
        facts_committed=facts_committed,
        steps_log=steps,
        artifacts=artifacts,
    )


class ScenePlan(BaseModel):
    motives: list[str] = Field(default_factory=list)
    conflict: str = ""
    key_bounds: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)


class SceneEvents(BaseModel):
    events: list[str] = Field(default_factory=list)
    dialogue_by_character: dict[str, list[str]] = Field(default_factory=dict)
    visible_outcome: str = ""


class FactDraft(BaseModel):
    facts: list[str] = Field(default_factory=list)
    status: str = "draft"  # draft | committed | rejected


class ValidateReport(BaseModel):
    hard_fails: list[str] = Field(default_factory=list)
    soft_notes: list[str] = Field(default_factory=list)


class ProseFacts(BaseModel):
    """从**最终正文**提取的事实与钩子状态。计划中的变化不能自动算发生。"""

    facts: list[str] = Field(default_factory=list, description="正文里已发生的不可逆事实")
    hooks_opened: list[str] = Field(default_factory=list, description="本章新开的钩子")
    hooks_closed: list[str] = Field(
        default_factory=list, description="本章兑现/关闭的既有钩子"
    )
    character_shifts: list[str] = Field(
        default_factory=list, description="人物态度/信念的实际变化"
    )


class WatchDecision(BaseModel):
    accept: bool = True
    revision_instruction: str = ""


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class BudgetMeter:
    call_cap: int = CALL_CAP_PER_SAMPLE
    cost_cap_minor: int | None = None
    calls: int = 0
    cost_minor: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    model_hint: str = "test"

    def ensure_capacity(self) -> None:
        if self.calls >= self.call_cap:
            raise BudgetExhausted(f"call_cap={self.call_cap}")

    def charge(self, usage: ModelUsage | None = None, *, model: str = "") -> None:
        self.ensure_capacity()
        self.calls += 1
        if usage is not None:
            from regent.novel.domain.price_book import actual_minor

            inp = int(getattr(usage, "input_tokens", 0) or 0)
            out = int(getattr(usage, "output_tokens", 0) or 0)
            cached = int(getattr(usage, "cached_input_tokens", 0) or 0)
            self.input_tokens += inp
            self.output_tokens += out
            self.cached_input_tokens += cached
            model_name = (model or self.model_hint or "test").strip() or "test"
            self.cost_minor += actual_minor(
                model_name,
                input_tokens=inp,
                output_tokens=out,
                cached_input_tokens=cached,
            )
        else:
            self.cost_minor += 1
        if self.cost_cap_minor is not None and self.cost_minor > self.cost_cap_minor:
            raise BudgetExhausted(f"cost_cap_minor={self.cost_cap_minor}")

    def cache_report(self) -> dict[str, Any]:
        from regent.novel.domain.price_book import cache_usage_report

        return cache_usage_report(
            self.model_hint or "test",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
        )

    @property
    def remaining_calls(self) -> int:
        return max(0, self.call_cap - self.calls)


@dataclass
class SamplePlan:
    fragment_id: str
    protocol: str
    call_cap: int
    max_revisions: int
    steps: list[str]
    purpose: str


@dataclass
class SampleResult:
    fragment_id: str
    protocol: str
    blind_id: str
    completed: bool
    calls_used: int
    cost_minor: int
    hard_fail_count: int
    used_revision: bool
    stop_reason: str
    prose: str
    facts: list[str]
    facts_committed: bool
    steps_log: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)


class MeteredProvider:
    """包装真实或模拟 provider，统一扣预算。"""

    def __init__(self, inner: ModelProvider, meter: BudgetMeter) -> None:
        self._inner = inner
        self._meter = meter

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        temperature: float = 0,
    ) -> StructuredModelResponse[Any]:
        self._meter.ensure_capacity()
        try:
            result = await self._inner.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
            )
        except Exception:
            # 发起过请求的失败也计入实验费用，避免用失败样本“省预算”。
            self._meter.charge()
            raise
        model = getattr(result, "model", "") or getattr(self._inner, "model_name", "") or ""
        if model and not self._meter.model_hint:
            self._meter.model_hint = str(model)
        elif model:
            self._meter.model_hint = str(model)
        self._meter.charge(result.usage, model=str(model or self._meter.model_hint))
        return result


class SimProvider:
    """确定性假模型：贯通协议步骤，不产生费用。"""

    model_name = "sim-quality-ab"

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        temperature: float = 0,
    ) -> StructuredModelResponse[Any]:
        name = response_model.__name__
        usage = ModelUsage(input_tokens=40, output_tokens=80, request_id="sim")
        if name == "SceneProse":
            # 从 user_prompt 抽标题痕迹，保证可区分样本
            title = "场景"
            for frag in FRAGMENTS:
                if frag["title"] in user_prompt or frag["id"] in user_prompt:
                    title = frag["title"]
                    break
            body = (
                f"【{title}】门缝里有风。他没有立刻回答，只把工具匣往身侧挪了一寸，"
                f"让来人看见匣底那道旧刮痕。选择已经发生，代价跟着落地。"
            )
# sim 达到最低长度即可；不再灌到 2500
            if len(body) < MIN_PROSE_CHARS:
                body = body + ("利害推进。" * ((MIN_PROSE_CHARS - len(body)) // 4 + 1))
                body = body[: TARGET_PROSE_CHARS]
            output: BaseModel = SceneProse(content=body)
        elif name == "ScenePlan":
            output = ScenePlan(
                motives=["试探对方底线", "保护尚未说出口的秘密"],
                conflict="当面必须表态，却不能无损过关",
                key_bounds=["不得旁白解释结局", "关键选择必须有可见代价"],
                must_not=["突然和解", "上帝视角泄底"],
            )
        elif name == "SceneEvents":
            output = SceneEvents(
                events=[
                    "来人提出要求",
                    "主角选择隐瞒关键信息并付出可见代价",
                    "关系温度下降，悬念未解",
                ],
                dialogue_by_character={
                    "来人": ["把东西交出来。"],
                    "主角": ["现在不行。"],
                },
                visible_outcome="要求未被满足；主角付出代价",
            )
        elif name == "FactDraft":
            output = FactDraft(
                facts=["主角拒绝当场交出关键物", "在场第三人听到了部分对话"],
                status="draft",
            )
        elif name == "ProseFacts":
            output = ProseFacts(
                facts=["主角做出带代价的选择并已落地", "关系温度发生变化", "新的威胁进入视野"],
                hooks_opened=["更大的局中人已注意到主角"],
                hooks_closed=["当场表态压力已解除"],
                character_shifts=["从被动应付转为主动承担风险"],
            )
        elif name == "ScenePlanDraft":
            from regent.novel.domain.scene_card import SceneCard, StructuredBeat
            from regent.novel.experiments.scene_exec import ScenePlanDraft as SPD

            output = SPD(
                cards=[
                    SceneCard(
                        scene_id="s1",
                        purpose="开场压力落地，主角被迫表态",
                        setting="录制现场",
                        entry_state="上一场压力已在门口",
                        protagonist_objective="在代价最小时保住底线",
                        opposition="对方逼其当场表态",
                        core_choice="交出线索 vs 承担风险",
                        emotion_arc="从被动到主动",
                        exit_change="关系与把柄发生变化",
                        beats=[
                            StructuredBeat(beat_id="b1", text="对方施加可见压力", scene_slot=0),
                            StructuredBeat(beat_id="b2", text="主角权衡后做出带代价的选择", scene_slot=0),
                        ],
                    ),
                    SceneCard(
                        scene_id="s2",
                        purpose="代价落地，钩子升起",
                        setting="录制后台",
                        entry_state="选择已做出",
                        protagonist_objective="消化代价",
                        opposition="新的威胁进入",
                        core_choice="是否暴露更多信息",
                        emotion_arc="从得意到警觉",
                        exit_change="更大威胁进入视野",
                        beats=[
                            StructuredBeat(beat_id="b3", text="对方回应，局面更紧", scene_slot=1),
                        ],
                    ),
                ],
                chapter_goal="关系与把柄发生变化，问题未解",
                continuity_notes="情绪从压迫到反杀再到警觉",
            )
        elif name == "SceneProseWithBeats":
            import re as _re
            from regent.novel.experiments.scene_exec import SceneProseWithBeats as SPWB

            body = (
                "【本场】门缝里有风。他没有立刻回答，只把工具匣往身侧挪了一寸，"
                "让来人看见匣底那道旧刮痕。选择已经发生，代价跟着落地。"
                "「你现在就选。」对方的声音压得很低。"
                "「……好。但这笔账另算。」他听见自己说。"
            )
            if len(body) < 300:
                body = body + ("利害推进，选择落地。" * ((300 - len(body)) // 7 + 1))
            # 从 user_prompt 抽出本场 beat_id；证据必须是正文真实摘录
            beat_ids = list(dict.fromkeys(_re.findall(r'"beat_id"\s*:\s*"(b\d+)"', user_prompt)))
            snippet = body[:24] if body else ""
            evidence = {bid: snippet for bid in beat_ids} or {"b1": snippet}
            output = SPWB(content=body, beat_evidence=evidence)
        elif name == "SceneAudit":
            import re as _re
            from regent.novel.domain.scene_card import BeatVerdict
            from regent.novel.experiments.scene_exec import SceneAudit as SA

            # 从场次卡抽出全部 beat_id；证据必须是 scene_text 真实摘录
            beat_ids = list(dict.fromkeys(_re.findall(r'"beat_id"\s*:\s*"(b\d+)"', user_prompt)))
            if not beat_ids:
                beat_ids = ["b1", "b2"]
            scene_m = _re.search(r'"scene_id"\s*:\s*"(s\d+)"', user_prompt)
            scene_id = scene_m.group(1) if scene_m else "s1"
            text_m = _re.search(r'"scene_text"\s*:\s*"((?:\\.|[^"\\])*)"', user_prompt)
            # _dump 使用 ensure_ascii=False，匹配串已是原中文，不要再 unicode_escape
            scene_text = text_m.group(1) if text_m else "门缝里有风"
            # 截取正文中真实存在的短摘录，避免 evidence_ungrounded 误伤 sim
            snippet = scene_text[:20] if scene_text else "门缝里有风"
            verdicts = [
                BeatVerdict(beat_id=bid, landed=True, evidence=snippet)
                for bid in beat_ids
            ]
            output = SA(
                scene_id=scene_id,
                continuity_ok=True,
                continuity_issues=[],
                beat_verdicts=verdicts,
                hard_fails=[],
                facts=["主角当场做出带代价的选择", "关系温度下降"],
                state_changes={"主角.立场": "从被动到主动"},
                character_shifts=["从应付转为主动承担风险"],
                hooks_opened=["更大威胁进入视野"],
                hooks_closed=["当场表态压力"],
            )
        elif name == "ValidateReport":
            # sim：默认通过，便于贯通；可用 user_prompt 注入 fail 标记测止损
            fails: list[str] = []
            if "FORCE_HARD_FAIL" in user_prompt:
                fails = ["sim_forced_hard_fail"]
            output = ValidateReport(hard_fails=fails, soft_notes=["sim_ok"])
        elif name == "WatchDecision":
            accept = "FORCE_REVISION" not in user_prompt
            output = WatchDecision(
                accept=accept,
                revision_instruction="" if accept else "加强选择瞬间的动作与停顿",
            )
        elif name == "ScriptAssignmentBoard":
            output = ScriptAssignmentBoard(
                goal_restated="写出可拍第一章，金手指当场兑现并留下压力",
                tasks=[
                    ScriptWriterTask(slot="alpha", task="公开场域里让信息差改写当面结果"),
                    ScriptWriterTask(slot="beta", task="先稳住局面，把反噬推到对手侧"),
                    ScriptWriterTask(slot="gamma", task="借第三方力量施压，少正面硬刚"),
                    ScriptWriterTask(slot="delta", task="小规模试探规则与代价，为后手留白"),
                ],
            )
        elif name == "ChapterScript":
            # 用槽位区分候选，避免依赖写死 route_*。
            if "candidate_slot\": \"delta\"" in user_prompt or '"candidate_slot": "delta"' in user_prompt:
                route = "丁"
            elif "candidate_slot\": \"gamma\"" in user_prompt or '"candidate_slot": "gamma"' in user_prompt:
                route = "丙"
            elif "candidate_slot\": \"beta\"" in user_prompt or '"candidate_slot": "beta"' in user_prompt:
                route = "乙"
            else:
                route = "甲"
            variants = {
                "甲": (
                    "金手指暴露给在场第三人",
                    "暴露",
                    "用金手指读出对方隐瞒的关键证据并当场反制",
                ),
                "乙": (
                    "亲情把柄被对方扣住",
                    "亲情",
                    "用金手指换来短暂信息差，却把家人推上赌桌",
                ),
                "丙": (
                    "把把柄交给第三方换施压",
                    "借刀",
                    "借剪辑/资方改写场上结果，主角少正面硬刚",
                ),
                "丁": (
                    "小试探付出可见代价",
                    "试探",
                    "只改一句/一个走位测规则，为后手留余地",
                ),
            }
            cost, cost_type, payoff = variants[route]
            mech = f"sim-{route}：用文抄公式信息差兑现「{payoff[:24]}」"
            output = ChapterScript(
                title_line=f"候选{route}",
                start_state="开场压力已在门口",
                end_change="关系与把柄发生变化，问题未解",
                protagonist_want="在代价最小时保住底线",
                opposition="对方逼其当场表态",
                beats=[
                    "对方施加可见压力",
                    "主角权衡后做出带代价的选择",
                    "对方回应，局面更紧",
                ],
                turning_point="选择不可收回",
                turning_reason="不选会立刻失去更重要的东西",
                cost=cost,
                cost_type=cost_type,
                key_dialogue=["你现在就选。", "……好。但这笔账另算。"],
                protagonist_knows="只知道对方想要的东西，不知其全盘意图",
                protagonist_weighs="交出会失去唯一线索；不交会当场受伤",
                reading_question="下一步还能不能翻盘",
                power_payoff=payoff,
                power_mechanism=mech,
                power_limits="sim：单次兑现须落到一条具体情报；不得实时发明未铺垫的未来",
                cast_draft=[
                    CastDraftPersona(
                        name="主角",
                        desire="护住底线",
                        relation="被逼到墙角",
                        knowledge="有限",
                        limit="不害孩子/同伴",
                        voice="短句，压着火",
                    )
                ],
            )
        elif name == "ScriptChoice":
            reject = "FORCE_REJECT_BOTH" in user_prompt
            output = ScriptChoice(
                selected_id="reject_both" if reject else "alpha",
                reason="alpha 的代价更非常规，钩子更深",
                weakness="对手动机仍可再锐利",
                must_land_beats=["当场代价", "主角权衡", "金手指兑现"],
                allow_writer_room="可加强感官与内心算计，不得改因果终点",
                direction_notes="贴主角视角；章末留压",
            )
        elif name in {"ChapterAccept", "ChapterCreativeAccept"}:
            output = ChapterAccept(
                accept="FORCE_REJECT_CHAPTER" not in user_prompt,
                fault="ok" if "FORCE_REJECT_CHAPTER" not in user_prompt else "render",
                notes="sim",
            )
        else:
            output = response_model()
        return StructuredModelResponse(output=output, usage=usage, model=self.model_name)


def fragment_by_id(fragment_id: str) -> dict[str, str]:
    for frag in FRAGMENTS:
        if frag["id"] == fragment_id:
            return frag
    raise KeyError(fragment_id)


def fragment_for_run(
    fragment_id: str,
    *,
    direction_keywords: Sequence[str] | None = None,
    direction_custom: Sequence[str] | None = None,
    use_default_when_empty: bool = True,
) -> dict[str, str]:
    from regent.novel.domain.story_direction import fragment_for_run as _apply

    base = fragment_by_id(fragment_id)
    return _apply(
        base,
        direction_keywords=direction_keywords,
        direction_custom=direction_custom,
        use_default_when_empty=use_default_when_empty,
    )


def build_matrix(
    *,
    protocols: tuple[str, ...] | None = None,
    fragments: tuple[str, ...] | None = None,
) -> list[SamplePlan]:
    letters = protocols or tuple(PROTOCOLS.keys())
    frag_ids = fragments or tuple(f["id"] for f in FRAGMENTS)
    rows: list[SamplePlan] = []
    for fid in frag_ids:
        fragment_by_id(fid)  # validate
        for letter in letters:
            proto = PROTOCOLS[letter]
            rows.append(
                SamplePlan(
                    fragment_id=fid,
                    protocol=letter,
                    call_cap=CALL_CAP_PER_SAMPLE,
                    max_revisions=MAX_REVISIONS,
                    steps=list(proto["steps"]),
                    purpose=str(proto["purpose"]),
                )
            )
    return rows


async def _call(
    provider: MeteredProvider,
    schema: type[BaseModel],
    system: str,
    payload: dict[str, Any],
) -> BaseModel:
    result = await provider.generate_structured(
        system_prompt=system,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        response_model=schema,
        temperature=0.4,
    )
    return result.output


def _fragment_payload(fragment: dict[str, str]) -> dict[str, Any]:
    kw_line = fragment.get("direction_keywords") or ""
    kw_list = [k for k in kw_line.split(",") if k.strip()] if kw_line else []
    return {
        "title": fragment["title"],
        "genre": fragment["genre"],
        "slot": fragment.get("slot", "chapter1_opening"),
        "channel": fragment.get("channel", "web_novel"),
        "direction": fragment.get("direction", ""),
        "direction_keywords": kw_list,
        "power": fragment.get("power", ""),
        "brief": fragment["brief"],
        "goal": fragment["goal"],
        "hook": fragment.get("hook", ""),
        "immersion": fragment.get("immersion", ""),
        "must_not": fragment.get("must_not", ""),
        "fragment_id": fragment["id"],
        "min_chars": MIN_PROSE_CHARS,
        "target_chars": TARGET_PROSE_CHARS,
        "max_dense_chars": MAX_DENSE_CHARS,
        "opening_contract": OPENING_CONTRACT,
        "scale_contract": SCALE_CONTRACT,
        "web_novel_power_contract": WEB_NOVEL_POWER_CONTRACT,
        "web_hook_contract": WEB_HOOK_CONTRACT,
        "immersion_contract": IMMERSION_CONTRACT,
        "density_contract": DENSITY_CONTRACT,
    }


def _write_system_a() -> str:
    return (
        "你是网文执笔者。第一章同时要强代入、强钩子、高密度、金手指兑现："
        "让读者钻进主角身体里替他做决定；禁止旁观讲故事；禁止注水凑字；禁止平凡无外挂开局。"
        + OPENING_CONTRACT
        + WEB_NOVEL_POWER_CONTRACT
        + WEB_HOOK_CONTRACT
        + IMMERSION_CONTRACT
        + DENSITY_CONTRACT
        + "不要写导演说明。"
    )


def _validate_system_opening() -> str:
    return (
        "按付费网文第一章标准核验。硬失败包括："
        "too_short；"
        "not_opening；"
        "unclear_subject；"
        "motive_incoherent；"
        "violates_must_not；"
        "weak_hook；"
        "flat_ending；"
        "late_cost；"
        "no_power（开场未见金手指兑现/信息差，像平凡日常）；"
        "no_immersion；"
        "pov_leak；"
        "padded。"
        "有一点代入但文字水、开头平、或主角无外挂，仍算失败。"
        f"短于{MIN_PROSE_CHARS}字记 too_short。"
    )


async def run_protocol_a(
    provider: MeteredProvider,
    fragment: dict[str, str],
    *,
    max_revisions: int = MAX_REVISIONS,
) -> SampleResult:
    steps: list[str] = ["assemble_goal"]
    artifacts: dict[str, Any] = {
        "goal": fragment["goal"],
        "slot": fragment.get("slot", "chapter1_opening"),
    }
    used_revision = False
    stop_reason = ""
    prose = ""
    hard_fails: list[str] = []
    try:
        steps.append("write_scene")
        written = await _call(
            provider,
            SceneProse,
            _write_system_a(),
            _fragment_payload(fragment),
        )
        assert isinstance(written, SceneProse)
        prose = written.content.strip()

        steps.append("validate_once")
        report = await _call(
            provider,
            ValidateReport,
            _validate_system_opening(),
            {
                **_fragment_payload(fragment),
                "prose": prose,
                "prose_chars": len(prose),
            },
        )
        assert isinstance(report, ValidateReport)
        hard_fails = list(report.hard_fails)
        if _prose_too_short(prose) and "too_short" not in hard_fails:
            hard_fails.append("too_short")
        artifacts["validation"] = report.model_dump()

        if (hard_fails or _prose_too_short(prose)) and max_revisions > 0:
            used_revision = True
            steps.append("rewrite_once")
            rewritten = await _call(
                provider,
                SceneProse,
                "根据硬失败修订网文第一章；不得另起一套主线。最多这一次修订。"
                + OPENING_CONTRACT
                + WEB_HOOK_CONTRACT
                + IMMERSION_CONTRACT
                + DENSITY_CONTRACT
                + f"目标约{TARGET_PROSE_CHARS}字，不少于{MIN_PROSE_CHARS}字。"
                "优先：强代入 + 删注水；不要为凑长度加段。",
                {
                    "prose": prose,
                    "hard_fails": hard_fails,
                    **_fragment_payload(fragment),
                },
            )
            assert isinstance(rewritten, SceneProse)
            prose = rewritten.content.strip()
            report2 = await _call(
                provider,
                ValidateReport,
                _validate_system_opening() + "再次核验修订稿。",
                {
                    "prose": prose,
                    "hard_fails_before": hard_fails,
                    **_fragment_payload(fragment),
                    "prose_chars": len(prose),
                },
            )
            assert isinstance(report2, ValidateReport)
            hard_fails = list(report2.hard_fails)
            if _prose_too_short(prose) and "too_short" not in hard_fails:
                hard_fails.append("too_short")
            artifacts["validation_after_revision"] = report2.model_dump()
    except BudgetExhausted as exc:
        stop_reason = f"budget:{exc}"
    except Exception as exc:  # noqa: BLE001 — 实验样本失败也要落盘
        stop_reason = f"error:{type(exc).__name__}:{exc}"

    length_fails = [f for f in hard_fails if f != "too_short"]
    return _finalize_result(
        fragment_id=fragment["id"],
        protocol="A",
        provider=provider,
        prose=prose,
        hard_fails=length_fails,
        used_revision=used_revision,
        stop_reason=stop_reason,
        facts=[],
        facts_committed=False,
        steps=steps,
        artifacts=artifacts,
    )


async def run_protocol_b(
    provider: MeteredProvider,
    fragment: dict[str, str],
    *,
    max_revisions: int = MAX_REVISIONS,
) -> SampleResult:
    """镜像生产：计划 → 结算事件 → 执笔呈现既定事件 → 复看 → 核验。"""
    steps: list[str] = []
    artifacts: dict[str, Any] = {}
    used_revision = False
    stop_reason = ""
    prose = ""
    hard_fails: list[str] = []
    try:
        steps.append("plan_scene")
        plan = await _call(
            provider,
            ScenePlan,
            "你是导演。给出本场人物动机、冲突与关键边界；不要写正文。",
            {
                "title": fragment["title"],
                "brief": fragment["brief"],
                "goal": fragment["goal"],
                "fragment_id": fragment["id"],
            },
        )
        assert isinstance(plan, ScenePlan)
        artifacts["plan"] = plan.model_dump()

        steps.append("scene_resolve")
        events = await _call(
            provider,
            SceneEvents,
            "按导演约束结算本场客观结果与关键台词。"
            "意图不是事实；不可为戏剧目标强行成功。"
            "将关键台词保存在 dialogue_by_character。",
            {"plan": plan.model_dump(), "brief": fragment["brief"]},
        )
        assert isinstance(events, SceneEvents)
        artifacts["events"] = events.model_dump()

        steps.append("render_prose")
        written = await _call(
            provider,
            SceneProse,
            "你是小说执笔者。按叙事指令将已发生的可见事件呈现为小说。"
            "禁止新增重大事件、能力、人物或知识。只能使用给定材料。"
            f"正文不少于{MIN_PROSE_CHARS}字；禁止把事件列表扩写成提纲体速写。",
            {
                "goal": fragment["goal"],
                "plan": plan.model_dump(),
                "events": events.model_dump(),
                "fragment_id": fragment["id"],
                "title": fragment["title"],
                "min_chars": MIN_PROSE_CHARS,
            },
        )
        assert isinstance(written, SceneProse)
        prose = written.content.strip()

        steps.append("watch_prose")
        watch = await _call(
            provider,
            WatchDecision,
            "导演复看正文：是否接受。若不接受，只给一次修订指令（表达层）。"
            f"若正文短于{MIN_PROSE_CHARS}字，必须要求扩写到足量。",
            {
                "prose": prose,
                "plan": plan.model_dump(),
                "events": events.model_dump(),
                "prose_chars": len(prose),
                "min_chars": MIN_PROSE_CHARS,
            },
        )
        assert isinstance(watch, WatchDecision)
        if _prose_too_short(prose):
            watch = WatchDecision(
                accept=False,
                revision_instruction=(
                    watch.revision_instruction
                    or f"扩写到至少{MIN_PROSE_CHARS}字，补足现场动作与对话节奏"
                ),
            )
        artifacts["watch"] = watch.model_dump()
        if not watch.accept and max_revisions > 0:
            used_revision = True
            steps.append("rewrite_once")
            rewritten = await _call(
                provider,
                SceneProse,
                "按导演修订指令改写；不得新增重大事件。只能使用给定材料。"
                f"正文不少于{MIN_PROSE_CHARS}字。",
                {
                    "prose": prose,
                    "revision_instruction": watch.revision_instruction,
                    "events": events.model_dump(),
                    "min_chars": MIN_PROSE_CHARS,
                },
            )
            assert isinstance(rewritten, SceneProse)
            prose = rewritten.content.strip()

        steps.append("validate")
        report = await _call(
            provider,
            ValidateReport,
            "核验正文是否覆盖已结算可见结果，有无越权新增事实。",
            {
                "prose": prose,
                "events": events.model_dump(),
                "plan": plan.model_dump(),
                "prose_chars": len(prose),
                "min_chars": MIN_PROSE_CHARS,
            },
        )
        assert isinstance(report, ValidateReport)
        hard_fails = list(report.hard_fails)
        artifacts["validation"] = report.model_dump()
    except BudgetExhausted as exc:
        stop_reason = f"budget:{exc}"
    except Exception as exc:  # noqa: BLE001
        stop_reason = f"error:{type(exc).__name__}:{exc}"

    return _finalize_result(
        fragment_id=fragment["id"],
        protocol="B",
        provider=provider,
        prose=prose,
        hard_fails=hard_fails,
        used_revision=used_revision,
        stop_reason=stop_reason,
        facts=list(artifacts.get("events", {}).get("events", []) or []),
        facts_committed=bool(prose) and not hard_fails and not _prose_too_short(prose),
        steps=steps,
        artifacts=artifacts,
    )


async def run_protocol_c(
    provider: MeteredProvider,
    fragment: dict[str, str],
    *,
    max_revisions: int = MAX_REVISIONS,
) -> SampleResult:
    """边界先行，写作后置抽事实；核验通过才视为可提交正式状态。"""
    steps: list[str] = []
    artifacts: dict[str, Any] = {}
    used_revision = False
    stop_reason = ""
    prose = ""
    hard_fails: list[str] = []
    facts: list[str] = []
    facts_committed = False
    try:
        steps.append("director_motives_and_bounds")
        plan = await _call(
            provider,
            ScenePlan,
            "你是导演。只给出人物动机、冲突与关键边界；不要结算具体事件台词，不要写正文。",
            {
                "title": fragment["title"],
                "brief": fragment["brief"],
                "goal": fragment["goal"],
                "fragment_id": fragment["id"],
            },
        )
        assert isinstance(plan, ScenePlan)
        artifacts["plan"] = plan.model_dump()

        steps.append("write_scene_within_bounds")
        written = await _call(
            provider,
            SceneProse,
            "你是小说执笔者。在给定动机与边界内完成场景。"
            "允许自然产生试探、反应与转折，但不得突破 must_not。"
            "不要写导演说明。"
            f"正文不少于{MIN_PROSE_CHARS}字；禁止提纲体或过短速写。",
            {
                "brief": fragment["brief"],
                "goal": fragment["goal"],
                "plan": plan.model_dump(),
                "fragment_id": fragment["id"],
                "title": fragment["title"],
                "min_chars": MIN_PROSE_CHARS,
            },
        )
        assert isinstance(written, SceneProse)
        prose = written.content.strip()

        steps.append("extract_draft_facts")
        draft = await _call(
            provider,
            FactDraft,
            "从正文提取需要进入世界状态的关键事实草稿；不要把文学姿态写成事实。",
            {"prose": prose, "plan": plan.model_dump()},
        )
        assert isinstance(draft, FactDraft)
        facts = list(draft.facts)
        artifacts["fact_draft"] = draft.model_dump()

        steps.append("validate_then_commit_facts")
        report = await _call(
            provider,
            ValidateReport,
            "核验草稿事实是否被正文支持，以及是否突破导演边界。通过后才可提交正式状态。"
            f"若正文短于{MIN_PROSE_CHARS}字，记硬失败 too_short。",
            {
                "prose": prose,
                "draft_facts": facts,
                "plan": plan.model_dump(),
                "prose_chars": len(prose),
                "min_chars": MIN_PROSE_CHARS,
            },
        )
        assert isinstance(report, ValidateReport)
        hard_fails = list(report.hard_fails)
        if _prose_too_short(prose) and "too_short" not in hard_fails:
            hard_fails.append("too_short")
        artifacts["validation"] = report.model_dump()

        if (hard_fails or _prose_too_short(prose)) and max_revisions > 0:
            used_revision = True
            steps.append("rewrite_once")
            rewritten = await _call(
                provider,
                SceneProse,
                "按硬失败与边界修订正文；修订后事实仍视为草稿。"
                f"必须写满至少{MIN_PROSE_CHARS}字。",
                {
                    "prose": prose,
                    "hard_fails": hard_fails,
                    "plan": plan.model_dump(),
                    "min_chars": MIN_PROSE_CHARS,
                },
            )
            assert isinstance(rewritten, SceneProse)
            prose = rewritten.content.strip()
            draft2 = await _call(
                provider,
                FactDraft,
                "重新提取事实草稿。",
                {"prose": prose},
            )
            assert isinstance(draft2, FactDraft)
            facts = list(draft2.facts)
            report2 = await _call(
                provider,
                ValidateReport,
                "再次核验。",
                {
                    "prose": prose,
                    "draft_facts": facts,
                    "plan": plan.model_dump(),
                    "prose_chars": len(prose),
                    "min_chars": MIN_PROSE_CHARS,
                },
            )
            assert isinstance(report2, ValidateReport)
            hard_fails = list(report2.hard_fails)
            if _prose_too_short(prose) and "too_short" not in hard_fails:
                hard_fails.append("too_short")
            artifacts["validation_after_revision"] = report2.model_dump()

        length_fails = [f for f in hard_fails if f != "too_short"]
        if not length_fails and not _prose_too_short(prose):
            facts_committed = True
            artifacts["facts_status"] = "committed"
        else:
            artifacts["facts_status"] = "rejected_remain_draft"
            hard_fails = length_fails
    except BudgetExhausted as exc:
        stop_reason = f"budget:{exc}"
    except Exception as exc:  # noqa: BLE001
        stop_reason = f"error:{type(exc).__name__}:{exc}"

    return _finalize_result(
        fragment_id=fragment["id"],
        protocol="C",
        provider=provider,
        prose=prose,
        hard_fails=hard_fails,
        used_revision=used_revision,
        stop_reason=stop_reason,
        facts=facts,
        facts_committed=facts_committed,
        steps=steps,
        artifacts=artifacts,
    )


def _script_system() -> str:
    return (
        "你是网文章节编剧，不是导演，也不是小说执笔者。"
        "输入里的 fragment/brief/power 是薄种子（舞台+能力机制），不是完整大纲。"
        "导演只给了宽松选题（writer_task）：请自行发明戏核、代价与转折；不要把选题扩写成死步骤。"
        "产出紧凑可演剧本：行动、回应、关键选择、转折原因、代价、结尾变化，"
        "并带少量关键对白与主角权衡。"
        "必须填写 power_mechanism、power_limits、cost_type、power_payoff；"
        "机制与代价须符合题材常理、真疼可兑现；选中后成为跨章正史。"
        "禁止只写提纲口号；禁止写成完整小说正文；禁止预写镜头表。"
        "剧本约500–900字信息量即可。"
        "人物草稿须含欲望、关系、认知、底线与金手指使用顾虑。"
        "本候选是草稿：不得假设已被选中或已进入正式世界。"
        + WEB_NOVEL_POWER_CONTRACT
        + SCRIPT_DIVERSITY_CONTRACT
    )


async def build_production_packet(
    provider: MeteredProvider,
    fragment: dict[str, str],
    *,
    protocol: str = PROTOCOL_SCRIPT,
    candidate_slots: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str], str]:
    """导演 BRIEF → 编剧×N → 选本 → 装配，不执笔、不核验、不验收。

    默认 N=4（甲乙丙丁），槽位只是编号；选题由导演布置，仍只选一本进入制作包。
    返回 ``(packet, artifacts, steps, stop_reason)``；packet 为 None 表示选本失败。
    """
    from regent.novel.domain.script_protocol import SCRIPT_CANDIDATE_SLOTS

    slots = tuple(candidate_slots or SCRIPT_CANDIDATE_SLOTS)
    steps: list[str] = []
    artifacts: dict[str, Any] = {
        "candidates": {},
        "rejected": {},
        "assignment": {},
        "fault_taxonomy": {"supply": None, "pick": None, "render": None},
        "candidate_slots": list(slots),
    }
    base = _fragment_payload(fragment)

    steps.append("director_brief")
    board = await _call(
        provider,
        ScriptAssignmentBoard,
        DIRECTOR_ASSIGN_CONTRACT,
        {
            **base,
            "candidate_slots": list(slots),
            "assign_note": (
                "给各槽位写一两句宽松选题；禁止写死情节步骤、代价类型、打脸清单。"
            ),
        },
    )
    assert isinstance(board, ScriptAssignmentBoard)
    assignment = normalize_assignment_board(board, slots=slots)
    artifacts["assignment"] = assignment
    artifacts["assignment_goal"] = str(board.goal_restated or "").strip()

    # 编剧 Hive：互不读稿，并发写 N 稿
    steps.append("write_scripts_hive")
    from regent.novel.domain.script_protocol import route_script_hive
    import asyncio

    slot_payloads = {
        slot: {
            **base,
            "writer_task": task_for_slot(assignment, slot),
            "assignment_goal": artifacts["assignment_goal"],
            "candidate_slot": slot,
            "note": "本请求只写这一条选题；自行发明戏核与代价；不得读取其他候选。",
        }
        for slot in slots
    }
    hive = route_script_hive(slot_payloads)
    artifacts["hive"] = hive

    async def _one(slot: str) -> tuple[str, ChapterScript]:
        draft = await _call(
            provider,
            ChapterScript,
            _script_system(),
            slot_payloads[slot],
        )
        assert isinstance(draft, ChapterScript)
        return slot, draft

    if hive.get("enabled"):
        written = await asyncio.gather(*(_one(slot) for slot in slots))
    else:
        written = [await _one(slot) for slot in slots]
    for slot, draft in written:
        steps.append(f"write_script_{slot}")
        artifacts["candidates"][slot] = draft.model_dump()

    steps.append("director_select")
    choice = await _call(
        provider,
        ScriptChoice,
        DIRECTOR_SELECT_CONTRACT,
        {
            **base,
            "candidates": dict(artifacts["candidates"]),
            "assignment": assignment,
            "assignment_goal": artifacts["assignment_goal"],
            "diversity_contract": SCRIPT_DIVERSITY_CONTRACT,
            "candidate_slots": list(slots),
        },
    )
    assert isinstance(choice, ScriptChoice)
    artifacts["director_choice"] = choice.model_dump()

    selected_id = str(choice.selected_id).strip().lower()
    if selected_id not in slots:
        artifacts["fault_taxonomy"]["supply"] = True
        artifacts["rejected"] = dict(artifacts["candidates"])
        artifacts["selected_id"] = ""
        return None, artifacts, steps, "reject_both_scripts"

    selected = ChapterScript.model_validate(artifacts["candidates"][selected_id])
    artifacts["rejected"] = {
        sid: data
        for sid, data in artifacts["candidates"].items()
        if sid != selected_id
    }
    artifacts["selected_id"] = selected_id
    steps.append("assemble_packet")
    packet = assemble_production_packet(
        selected=selected,
        choice=choice,
        meta={
            "fragment_id": fragment.get("id"),
            "protocol": protocol,
            "candidate_count": len(slots),
        },
    )
    artifacts["production_packet"] = packet
    assert_rejected_isolated(
        packet,
        artifacts["rejected"],
        selected=selected,
    )
    return packet, artifacts, steps, ""


async def run_protocol_s(
    provider: MeteredProvider,
    fragment: dict[str, str],
    *,
    max_revisions: int = MAX_REVISIONS,
    fixed_packet: dict[str, Any] | None = None,
) -> SampleResult:
    """两剧本择优实验切片：编剧×N → 导演选本 → 装配 → 执笔 → 核验 → 导演验收。

    ``fixed_packet``：对照实验用——直接使用预生成的制作包，跳过编剧与选本，
    使 S 与 X 在同一份选定剧本上比较执笔方式差异。
    """
    steps: list[str] = []
    artifacts: dict[str, Any] = {
        "protocol_name": "script_select_then_write",
        "candidates": {},
        "rejected": {},
        "fault_taxonomy": {"supply": None, "pick": None, "render": None},
    }
    used_revision = False
    stop_reason = ""
    prose = ""
    hard_fails: list[str] = []
    facts: list[str] = []
    facts_committed = False
    try:
        base = _fragment_payload(fragment)
        if fixed_packet is not None:
            # 固定剧本对照：跳过编剧与选本
            steps.append("use_fixed_packet")
            packet = fixed_packet
            selected = ChapterScript.model_validate(packet.get("script") or {})
            choice = ScriptChoice.model_validate(packet.get("direction") or {})
            selected_id = str(choice.selected_id or "alpha")
            artifacts["candidates"] = {"fixed": selected.model_dump()}
            artifacts["selected_id"] = "fixed"
            artifacts["production_packet"] = packet
            artifacts["fixed_packet"] = True
        else:
            packet, prep_artifacts, prep_steps, prep_stop = await build_production_packet(
                provider, fragment, protocol=PROTOCOL_SCRIPT
            )
            steps.extend(prep_steps)
            artifacts["candidates"].update(prep_artifacts.get("candidates") or {})
            artifacts["director_choice"] = prep_artifacts.get("director_choice")
            artifacts["assignment"] = prep_artifacts.get("assignment") or {}
            artifacts["assignment_goal"] = prep_artifacts.get("assignment_goal") or ""
            artifacts["hive"] = prep_artifacts.get("hive")
            artifacts["rejected"].update(prep_artifacts.get("rejected") or {})
            artifacts["fault_taxonomy"].update(prep_artifacts.get("fault_taxonomy") or {})
            if packet is None or prep_stop:
                artifacts["selected_id"] = prep_artifacts.get("selected_id") or ""
                return _finalize_result(
                    fragment_id=fragment["id"],
                    protocol="S",
                    provider=provider,
                    prose="",
                    hard_fails=["no_viable_script"],
                    used_revision=False,
                    stop_reason=prep_stop or "reject_both_scripts",
                    facts=[],
                    facts_committed=False,
                    steps=steps,
                    artifacts=artifacts,
                )
            selected = ChapterScript.model_validate(packet.get("script") or {})
            choice = ScriptChoice.model_validate(packet.get("direction") or {})
            selected_id = str(choice.selected_id or "alpha")
            artifacts["selected_id"] = prep_artifacts.get("selected_id") or selected_id
            artifacts["production_packet"] = packet

        steps.append("write_chapter")
        written = await _call(
            provider,
            SceneProse,
            "你是小说执笔者。按选定剧本与导演执导要求，完成整章文学演绎。"
            "统合动作、对白与心理；锁定单一主角有限视角。"
            "必须让金手指在正文中被读者「看见兑现」，不能只靠说明。"
            "不得改写剧本因果终点与 must_land_beats；不得引入弃选剧本情节。"
            + OPENING_CONTRACT
            + WEB_NOVEL_POWER_CONTRACT
            + WEB_HOOK_CONTRACT
            + IMMERSION_CONTRACT
            + DENSITY_CONTRACT,
            {
                **base,
                "production_packet": packet,
            },
        )
        assert isinstance(written, SceneProse)
        prose = written.content.strip()

        steps.append("validate")
        report = await _call(
            provider,
            ValidateReport,
            "核验正文是否兑现选定剧本的关键节拍与代价，有无越权新增事实。"
            "文学姿态不是硬失败；突破剧本终点或引入弃选路线是硬失败。"
            f"短于{MIN_PROSE_CHARS}字记 too_short；注水记 padded。",
            {
                "prose": prose,
                "selected_script": selected.model_dump(),
                "must_land_beats": choice.must_land_beats,
                "prose_chars": len(prose),
                "min_chars": MIN_PROSE_CHARS,
            },
        )
        assert isinstance(report, ValidateReport)
        hard_fails = list(report.hard_fails)
        if _prose_too_short(prose) and "too_short" not in hard_fails:
            hard_fails.append("too_short")
        artifacts["validation"] = report.model_dump()

        if (hard_fails or _prose_too_short(prose)) and max_revisions > 0:
            used_revision = True
            steps.append("rewrite_once")
            rewrite_instruction = "；".join(hard_fails[:6]) or "加强代入与钩子兑现"
            rewritten = await _call(
                provider,
                SceneProse,
                "你是修订执笔者。下面给出旧稿与核验问题，请针对问题修改，"
                "不要凭空重写；不得改剧本终点；删注水；加强代入。"
                + DENSITY_CONTRACT
                + IMMERSION_CONTRACT,
                {
                    "previous_draft": prose,
                    "revision_instruction": rewrite_instruction,
                    "hard_fails": hard_fails,
                    "production_packet": packet,
                    "min_chars": MIN_PROSE_CHARS,
                    "target_chars": TARGET_PROSE_CHARS,
                },
            )
            assert isinstance(rewritten, SceneProse)
            prose = rewritten.content.strip()
            report2 = await _call(
                provider,
                ValidateReport,
                "再次核验。",
                {
                    "prose": prose,
                    "selected_script": selected.model_dump(),
                    "must_land_beats": choice.must_land_beats,
                    "prose_chars": len(prose),
                    "min_chars": MIN_PROSE_CHARS,
                },
            )
            assert isinstance(report2, ValidateReport)
            hard_fails = list(report2.hard_fails)
            if _prose_too_short(prose) and "too_short" not in hard_fails:
                hard_fails.append("too_short")
            artifacts["validation_after_revision"] = report2.model_dump()

        length_fails = [f for f in hard_fails if f != "too_short"]
        steps.append("director_accept")
        accept = await _call(
            provider,
            ChapterAccept,
            "你是总导演，做整章创作验收（不是事实核验器）。"
            "判断读者是否愿意继续、能否代入主角权衡。"
            "若失败，fault 必须三选一：supply（候选本就没有好戏）、"
            "pick（有更好候选却选错）、render（剧本可拍但正文写坏）。"
            "通过则 fault=ok。",
            {
                "prose": prose,
                "selected_id": selected_id,
                "direction": choice.model_dump(),
                "validation_hard_fails": length_fails,
            },
        )
        assert isinstance(accept, ChapterAccept)
        artifacts["director_accept"] = accept.model_dump()
        fault = (accept.fault or "").strip().lower()
        if fault in {"supply", "pick", "render"}:
            artifacts["fault_taxonomy"][fault] = True
        if length_fails:
            artifacts["fault_taxonomy"]["render"] = True
        if accept.accept and not length_fails and not _prose_too_short(prose):
            # 事实必须从**最终正文**提取，不能把选定剧本的计划当事实承接。
            steps.append("extract_facts_from_prose")
            prose_facts = await _call(
                provider,
                ProseFacts,
                "从这段最终正文中提取已发生的不可逆事实、新开/关闭的钩子、人物态度变化。"
                "只提取正文里读者能看见的结果；剧本计划但正文未呈现的不算。",
                {
                    "prose": prose,
                    "selected_script_end_change": selected.end_change,
                    "selected_script_cost": selected.cost,
                },
            )
            assert isinstance(prose_facts, ProseFacts)
            facts = [f for f in prose_facts.facts if f and f.strip()]
            artifacts["prose_facts"] = prose_facts.model_dump()
            artifacts["hooks_opened"] = list(prose_facts.hooks_opened)
            artifacts["hooks_closed"] = list(prose_facts.hooks_closed)
            artifacts["character_shifts"] = list(prose_facts.character_shifts)
            if not facts:
                # 正文通过验收却抽不出事实：不能把剧本结尾当事实硬灌。
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
            hard_fails = length_fails
    except BudgetExhausted as exc:
        stop_reason = f"budget:{exc}"
    except Exception as exc:  # noqa: BLE001
        stop_reason = f"error:{type(exc).__name__}:{exc}"

    return _finalize_result(
        fragment_id=fragment["id"],
        protocol="S",
        provider=provider,
        prose=prose,
        hard_fails=hard_fails,
        used_revision=used_revision,
        stop_reason=stop_reason,
        facts=facts,
        facts_committed=facts_committed,
        steps=steps,
        artifacts=artifacts,
    )


_RUNNERS = {
    "A": run_protocol_a,
    "B": run_protocol_b,
    "C": run_protocol_c,
    "S": run_protocol_s,
}


def _run_protocol_x_lazy(provider: MeteredProvider, fragment: dict[str, str], *, max_revisions: int = 1) -> SampleResult:
    """协议 X：懒加载，避免 scene_exec ↔ quality_ab 循环导入。"""
    from regent.novel.experiments.scene_exec import run_protocol_x

    return run_protocol_x(provider, fragment, max_revisions=max_revisions)


_RUNNERS["X"] = _run_protocol_x_lazy


async def run_sample(
    *,
    protocol: str,
    fragment_id: str,
    provider: ModelProvider,
    call_cap: int = CALL_CAP_PER_SAMPLE,
    max_revisions: int = MAX_REVISIONS,
) -> SampleResult:
    letter = protocol.upper()
    if letter not in _RUNNERS:
        raise ValueError(f"unknown protocol: {protocol}")
    fragment = fragment_by_id(fragment_id)
    meter = BudgetMeter(call_cap=call_cap)
    metered = MeteredProvider(provider, meter)
    result = await _RUNNERS[letter](metered, fragment, max_revisions=max_revisions)
    result.blind_id = hashlib.sha256(
        f"{fragment_id}:{letter}:{uuid.uuid4().hex}".encode()
    ).hexdigest()[:12]
    return result


def summarize_results(results: list[SampleResult]) -> dict[str, Any]:
    by_protocol: dict[str, list[SampleResult]] = {k: [] for k in PROTOCOLS}
    for row in results:
        by_protocol.setdefault(row.protocol, []).append(row)

    def arm_stats(rows: list[SampleResult]) -> dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        return {
            "n": n,
            "completion_rate": sum(1 for r in rows if r.completed) / n,
            "avg_calls": sum(r.calls_used for r in rows) / n,
            "avg_cost_minor": sum(r.cost_minor for r in rows) / n,
            "hard_fail_rate": sum(1 for r in rows if r.hard_fail_count) / n,
            "revision_rate": sum(1 for r in rows if r.used_revision) / n,
        }

    arms = {letter: arm_stats(rows) for letter, rows in by_protocol.items() if rows}
    # 自动判定只看完成率/费用；盲评需人工填表后另算。
    verdict_hint = (
        "Await blind scores. If B does not beat A on want_to_continue under similar "
        "budget, shrink director to scene design + key decisions."
    )
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "arms": arms,
        "verdict_hint": verdict_hint,
        "sample_count": len(results),
    }


def result_public_dict(result: SampleResult) -> dict[str, Any]:
    """含协议字母的完整记录（评委不可见）。"""
    return asdict(result)


def result_blind_dict(result: SampleResult) -> dict[str, Any]:
    """盲评可见：无协议字母。"""
    return {
        "blind_id": result.blind_id,
        "fragment_id": result.fragment_id,
        "prose": result.prose,
        "completed": result.completed,
        "calls_used": result.calls_used,
        "cost_minor": result.cost_minor,
        "hard_fail_count": result.hard_fail_count,
        "used_revision": result.used_revision,
        "stop_reason": result.stop_reason,
    }
