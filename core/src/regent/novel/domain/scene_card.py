"""导演分场：把选定剧本编译成可执行的场景卡与结构化节拍。

设计要点（Codex 方案 §四）：

- 每个节拍有稳定 ID，可关联正文证据；"证据存在"不能只查关键词。
- 场景卡回答：入场状态、人物目的、行动与回应、核心选择、情绪变化、出场变化、表现要求。
- 计划中的变化不能自动算发生——场记从正文提取实际变化后才写入临时状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from regent.novel.domain.script_protocol import ChapterScript, ScriptChoice


class StructuredBeat(BaseModel):
    """带稳定 ID 的结构化节拍。执笔与验收共用同一 ID。"""

    beat_id: str = Field(min_length=1, description="稳定 ID，如 b1/b2；整章内唯一")
    text: str = Field(min_length=1, description="节拍内容：谁做什么、结果如何")
    scene_slot: int = Field(default=0, ge=0, description="归属场景序号（0-based）")
    actor: str = Field(default="", description="推动本节拍的人物名")
    precondition: str = Field(default="", description="本节拍成立的前提")
    must_show: bool = Field(
        default=True,
        description="是否必须在正文展开（False 可略写/一笔带过）",
    )
    evidence_hint: str = Field(
        default="",
        description="验收时应看到的正文方向（非关键词匹配）",
    )


class SceneCard(BaseModel):
    """一张可执行的场景卡：导演把剧本拆成的"一场完整的戏"。"""

    scene_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1, description="本场必须改变什么")
    setting: str = Field(default="", description="地点/时间/氛围一句话")
    entry_state: str = Field(default="", description="入场：谁知道什么、关系、资源")
    protagonist_objective: str = Field(default="", description="主角想得到什么")
    opposition: str = Field(default="", description="对手为什么阻止")
    core_choice: str = Field(default="", description="主角最终舍弃什么、选择什么")
    emotion_arc: str = Field(default="", description="从什么感受到什么，由何触发")
    exit_change: str = Field(default="", description="出场：哪件事已不可逆")
    must_expand: list[str] = Field(
        default_factory=list, description="必须展开的瞬间"
    )
    may_compress: list[str] = Field(
        default_factory=list, description="只需略写的信息"
    )
    beats: list[StructuredBeat] = Field(default_factory=list)
    target_chars: int = Field(default=600, ge=200, le=2000)


class ChapterScenePlan(BaseModel):
    """整章分场计划：2–4 张场景卡，由导演从选定剧本编译。"""

    chapter_no: int = 0
    cards: list[SceneCard] = Field(min_length=1, max_length=6)
    chapter_goal: str = Field(default="", description="本章整体必须改变什么")
    continuity_notes: str = Field(
        default="", description="跨场连续性注意：情绪/道具/信息隔离"
    )


class SceneWriteResult(BaseModel):
    """单场执笔产物。"""

    scene_id: str = Field(min_length=1)
    content: str = Field(min_length=50)
    beat_evidence: dict[str, str] = Field(
        default_factory=dict,
        description="beat_id → 正文证据摘录（场记填写）",
    )


class SceneDeltas(BaseModel):
    """场记从正文提取的实际变化。计划中的变化不能自动算发生。"""

    scene_id: str = Field(min_length=1)
    facts: list[str] = Field(default_factory=list, description="已发生的不可逆事实")
    state_changes: dict[str, str] = Field(
        default_factory=dict, description="实体.属性 → 新值"
    )
    character_shifts: list[str] = Field(
        default_factory=list, description="人物态度/信念的实际变化"
    )
    hooks_opened: list[str] = Field(default_factory=list)
    hooks_closed: list[str] = Field(default_factory=list)
    beats_landed: list[str] = Field(
        default_factory=list, description="已兑现的 beat_id"
    )
    beats_missed: list[str] = Field(default_factory=list)
    notes: str = ""


class BeatVerdict(BaseModel):
    beat_id: str = Field(min_length=1)
    landed: bool = False
    evidence: str = Field(default="", description="正文摘录；landed=True 时必填")
    note: str = ""


def evidence_grounded(evidence: str, scene_text: str) -> bool:
    """摘录是否出现在正文中（含省略号分段；允许标点/空白差异）。"""
    quote = (evidence or "").strip()
    text = (scene_text or "").strip()
    if not quote or not text:
        return False
    if quote in text:
        return True
    # 与生产 _is_grounded 对齐：允许 A……B 跳读，但每段都要接地
    import re

    parts = [p.strip() for p in re.split(r"…|\.{2,}|．{2,}", quote) if p.strip()]
    if len(parts) > 1 and all(evidence_grounded(p, text) for p in parts):
        return True

    def _fold(s: str) -> str:
        return re.sub(
            r"[\s\u3000，。！？、；：\"\"''「」『』《》【】（）()\[\]…·.,!?;:'\"\-—_]+",
            "",
            s,
        )

    fq, ft = _fold(quote), _fold(text)
    if len(fq) >= 8 and fq in ft:
        return True
    # 长摘录：任意连续 12 字折叠窗口命中即可（场记常改标点/略缩）
    window = min(16, len(fq))
    if window >= 12:
        for i in range(0, len(fq) - window + 1):
            if fq[i : i + window] in ft:
                return True
    return False


def normalize_opening_must_show(
    plan: ChapterScenePlan, *, first_scene_must_cap: int = 2
) -> ChapterScenePlan:
    """多场时限制首场硬卡节拍数，避免导演把整章 must 塞进 s1 劝退开篇。"""
    if len(plan.cards) < 2:
        return plan
    cards: list[SceneCard] = []
    for si, card in enumerate(plan.cards):
        new_beats: list[StructuredBeat] = []
        for i, beat in enumerate(card.beats):
            must = bool(beat.must_show)
            if si == 0:
                must = i < first_scene_must_cap
            new_beats.append(beat.model_copy(update={"must_show": must}))
        cards.append(card.model_copy(update={"beats": new_beats}))
    return plan.model_copy(update={"cards": cards})


def ground_beat_evidence(
    verdicts: list[BeatVerdict], scene_text: str
) -> list[str]:
    """程序核对接地：landed 必须有摘录且摘录在正文。原地降级未接地项。

    返回失败标签列表（evidence_missing / evidence_ungrounded）。
    生产与实验必须共用此判定，避免行为分叉。
    """
    fails: list[str] = []
    for verdict in verdicts:
        if not verdict.landed:
            continue
        ev = (verdict.evidence or "").strip()
        if not ev:
            verdict.landed = False
            verdict.note = (verdict.note or "") + "|缺少证据摘录"
            fails.append(f"evidence_missing:{verdict.beat_id}")
        elif not evidence_grounded(ev, scene_text):
            verdict.landed = False
            verdict.note = (verdict.note or "") + "|证据不在正文"
            fails.append(f"evidence_ungrounded:{verdict.beat_id}")
    return fails


@dataclass
class SceneAuditDecision:
    """场记程序门控结果：生产与实验共用，避免分叉。"""

    critical_missed: list[str]
    evidence_fails: list[str]
    continuity_block: bool
    hard_fails: list[str]
    continuity_issues: list[str]

    @property
    def blocking(self) -> bool:
        """硬阻断：缺 must 节拍 / 模型硬失败 / 连续性。

        evidence_fails 单独出现时，若已反映进 critical_missed（接地降级），
        不再另计；仅 evidence 标签残留且无缺拍时不阻断——避免「摘录格式」空杀开篇。
        """
        return bool(
            self.critical_missed
            or self.hard_fails
            or self.continuity_block
        )

    def needs_revision(self) -> bool:
        """需要场内修订：硬阻断，或证据接地问题。"""
        return self.blocking or bool(self.evidence_fails)

    def revision_instruction(self, *, limit: int = 400) -> str:
        parts: list[str] = []
        if self.critical_missed:
            parts.append(f"未落地节拍：{'、'.join(self.critical_missed)}")
        if self.hard_fails:
            parts.append(f"硬失败：{'；'.join(self.hard_fails[:3])}")
        if self.continuity_block:
            parts.append(f"连续性：{'；'.join(self.continuity_issues[:2])}")
        if self.evidence_fails:
            parts.append(f"证据：{'、'.join(self.evidence_fails[:3])}")
        return "；".join(parts)[:limit]

    def hard_fail_tags(self, scene_id: str) -> list[str]:
        tags: list[str] = []
        tags.extend(f"{scene_id}:{h}" for h in self.hard_fails[:3])
        if self.continuity_block:
            tags.extend(f"{scene_id}:cont:{c}" for c in self.continuity_issues[:2])
        tags.extend(f"{scene_id}:{e}" for e in self.evidence_fails[:3])
        if self.critical_missed:
            tags.append(
                f"{scene_id}:beats_missed:{','.join(self.critical_missed[:4])}"
            )
        return tags


def is_empty_state_discovery_false_positive(
    issue: str,
    *,
    working_state: dict[str, Any] | None = None,
) -> bool:
    """空入场状态下，场记常把「本场计划内的发现」误判成连续性硬伤。

    典型句式（实跑 df765114）：
    「入场状态中沈默不知道黄铜小钥匙的存在，但正文中他从座钟底座摸出钥匙」
    空 ``working_state`` 表示尚无已确认事实，不等于角色对一切将发现之物知情为假。

    **非空已确认状态时一律返回 False**：不得凭「入场状态/不知道」关键词软化真实知识矛盾。
    """
    if working_state is not None and isinstance(working_state, dict) and working_state:
        return False
    # 调用方未传入 working_state 时，不得仅凭文案判定误报（缺证据则保硬失败）
    if working_state is None:
        return False
    text = str(issue or "").strip()
    if not text:
        return False
    if "入场状态" in text and ("不知道" in text or "不知情" in text or "并不知道" in text):
        return True
    if "缺少发现" in text:
        return True
    return False


def is_entry_vs_ending_state_lag(issue: str) -> bool:
    """入场状态摘要与上场结尾正文互相打架：多半是 working_state 滞后，不是新正文硬伤。

    实跑 858ad0b6 ch2：上场结尾已写铁链落地/怀表停转，入场状态仍留锁死/倒转，
    场记据此硬拦本场。应以已接受上场结尾为准，不因摘要滞后停机。
    """
    text = str(issue or "").strip()
    if not text:
        return False
    return "入场状态" in text and "上场结尾" in text


def continuity_should_block(
    *,
    continuity_ok: bool,
    continuity_issues: list[str] | None,
    working_state: dict[str, Any] | None = None,
) -> bool:
    """连续性是否硬阻断。空入场发现误判、入场/上场结尾摘要滞后 → 不阻断。"""
    issues = [str(x).strip() for x in (continuity_issues or []) if str(x).strip()]
    if continuity_ok or not issues:
        return False
    state = working_state if isinstance(working_state, dict) else {}
    if not state and all(
        is_empty_state_discovery_false_positive(i, working_state=state) for i in issues
    ):
        return False
    if all(is_entry_vs_ending_state_lag(i) for i in issues):
        return False
    return True


def evaluate_scene_audit(
    *,
    must_beat_ids: set[str] | list[str],
    verdicts: list[BeatVerdict],
    hard_fails: list[str] | None,
    continuity_ok: bool,
    continuity_issues: list[str] | None,
    scene_text: str,
    working_state: dict[str, Any] | None = None,
) -> SceneAuditDecision:
    """补全缺失 must 判定 → 证据接地 → 汇总是否阻断放行。"""
    must_ids = set(must_beat_ids)
    verdict_ids = {v.beat_id for v in verdicts}
    for missing in sorted(must_ids - verdict_ids):
        verdicts.append(
            BeatVerdict(beat_id=missing, landed=False, note="场记未给出判定")
        )
    evidence_fails = ground_beat_evidence(verdicts, scene_text)
    critical_missed = [
        v.beat_id for v in verdicts if not v.landed and v.beat_id in must_ids
    ]
    issues = list(continuity_issues or [])
    return SceneAuditDecision(
        critical_missed=critical_missed,
        evidence_fails=evidence_fails,
        continuity_block=continuity_should_block(
            continuity_ok=continuity_ok,
            continuity_issues=issues,
            working_state=working_state,
        ),
        hard_fails=list(hard_fails or []),
        continuity_issues=issues,
    )


# 生产与实验共用的单场执笔 system（动态字数/修订只进 payload）。
SCENE_WRITE_SYSTEM = (
    "你是小说执笔者，只写这一场戏。统合动作、对白与心理；锁定单一主角有限视角。"
    "必须让本场 must_show 节拍在正文中被读者看见。不得改写本场 core_choice 的因果终点。"
    "beat_evidence 给出每个 beat_id 在正文中的证据摘录（须能在正文原样找到）。"
    "目标字数见 payload.target_chars；禁止注水，也禁止为赶节拍写跳跃提纲体。"
    "若本场是开篇章首场：先让读者站稳（谁/哪/面对什么）并讲清金手指规则，再推进兑现；"
    "冲突规模匹配当场，禁止小题大做。"
    "禁止同一犹豫动作无进展地连写三遍；禁止把上场已交代的穿越/身体陌生感再开一次头。"
    "每场只增加新的选择、信息或后果；已成立的事实用一句承接，不要整段复读。"
    "若 payload.is_revision 为真：这是定点修订，不是重写整章。"
    "严格按 revision_instruction 与 repair_ticket 执行："
    "1) expected_actions 含 remove_dup 时：must_remove_quotes 在本场至多保留一处，优先删后出现的复读；"
    "2) expected_actions 含 rewrite_scene 时：按 forbidden_claims / evidence_quotes 纠正错误断言，"
    "不得把证据句当成「必须删掉的重复摘录」；正确状态应写入正文；"
    "3) 多个动作并存时先去重再纠错；不要整场从零重写；"
    "4) 不要为「显得改过」而改写无关段落；不要引入未证实事实；"
    "5) 输出仍是完整本场正文，不是补丁说明。"
)


class SceneSupervisorReport(BaseModel):
    """场记/连续性核验：本场是否接得上、节拍是否真落地。"""

    scene_id: str = Field(min_length=1)
    continuity_ok: bool = True
    continuity_issues: list[str] = Field(default_factory=list)
    beat_verdicts: list[BeatVerdict] = Field(default_factory=list)
    hard_fails: list[str] = Field(default_factory=list)


class ChapterAssembled(BaseModel):
    """整章剪辑产物。"""

    title: str = ""
    content: str = Field(min_length=1)
    scene_boundaries: list[int] = Field(
        default_factory=list, description="各场在合并正文中的起始偏移"
    )


def compile_scene_plan(
    script: ChapterScript,
    choice: ScriptChoice,
    *,
    chapter_no: int = 0,
    prior_summary: str = "",
) -> ChapterScenePlan:
    """把选定剧本确定性编译成 2–4 张场景卡的骨架。

    完整分场（补全 entry_state / core_choice 等）由导演模型完成；
    此函数只做结构拆分，保证节拍有 ID、有归属，不凭空发明情节。
    """
    beats = list(script.beats or [])
    if not beats:
        beats = [script.turning_point or script.end_change or "本章核心变化"]
    # 骨架必须自带关键叙述，否则后续覆盖校验会对「程序生成的节拍」误伤。
    for key in (
        (script.turning_point or "").strip(),
        (script.cost or "").strip(),
        (script.end_change or "").strip(),
    ):
        if key and not _text_covers(key, beats, min_frag=8):
            beats.append(key)

    # 按转折点切成 2–4 场：转折前 / 转折 / 转折后（代价与钩子）。
    turning = (script.turning_point or "").strip()
    turning_idx = next(
        (i for i, b in enumerate(beats) if turning and turning in b),
        max(0, len(beats) // 2),
    )
    groups: list[list[int]] = []
    if len(beats) <= 2:
        groups = [list(range(len(beats)))]
    elif len(beats) == 3:
        groups = [[0], [1, 2]]
    else:
        mid = min(turning_idx, len(beats) - 2)
        groups = [
            list(range(0, mid)),
            list(range(mid, min(mid + 2, len(beats)))),
            list(range(min(mid + 2, len(beats)), len(beats))),
        ]
        groups = [g for g in groups if g]
        if len(groups) > 4:
            groups = groups[:3] + [sum(groups[3:], [])]

    total = max(1, len(beats))
    chapter_budget = 2200
    cards: list[SceneCard] = []
    for si, idxs in enumerate(groups):
        s_beats: list[StructuredBeat] = []
        for local_i, bi in enumerate(idxs):
            # 首场只硬卡前两拍：开篇站稳+规则露头；转折/代价放在后场硬卡，避免 s1 过载劝退
            if len(groups) == 1:
                must = True
            elif si == 0:
                must = local_i < min(2, len(idxs))
            else:
                must = True
            s_beats.append(
                StructuredBeat(
                    beat_id=f"b{bi + 1}",
                    text=beats[bi],
                    scene_slot=si,
                    must_show=must,
                )
            )
        share = sum(1 for _ in idxs) / total
        # 首场略抬字数：把「谁/哪/金手指规则」写清楚
        cards.append(
            SceneCard(
                scene_id=f"s{si + 1}",
                purpose=script.end_change if si == len(groups) - 1 else (beats[idxs[0]] if idxs else ""),
                entry_state=prior_summary if si == 0 else "承接上一场已发生的变化",
                protagonist_objective=script.protagonist_want,
                opposition=script.opposition,
                exit_change=script.end_change if si == len(groups) - 1 else "",
                beats=s_beats,
                target_chars=max(
                    500,
                    min(
                        1400,
                        int(chapter_budget * share) + (200 if si == 0 else 0),
                    ),
                ),
            )
        )
    return ChapterScenePlan(
        chapter_no=chapter_no,
        cards=cards,
        chapter_goal=script.end_change,
        continuity_notes=(
            f"转折：{script.turning_point or '无'}；代价：{script.cost or '无'}；"
            f"金手指兑现：{script.power_payoff or '无'}"
        ),
    )


def beats_of_plan(plan: ChapterScenePlan) -> list[StructuredBeat]:
    out: list[StructuredBeat] = []
    for card in plan.cards:
        out.extend(card.beats)
    return out


def beat_index(plan: ChapterScenePlan) -> dict[str, StructuredBeat]:
    return {b.beat_id: b for b in beats_of_plan(plan)}


def must_land_ids(
    plan: ChapterScenePlan, must_land: list[str] | None = None
) -> list[str]:
    """必须落地的 beat_id。

    若导演给出了 must_land_beats 文本，优先用文本匹配节拍；
    否则默认全部 must_show 节拍。
    """
    all_beats = beats_of_plan(plan)
    if not must_land:
        return [b.beat_id for b in all_beats if b.must_show]
    ids: list[str] = []
    for text in must_land:
        text = (text or "").strip()
        if not text:
            continue
        hit = next(
            (
                b
                for b in all_beats
                if text in b.text
                or b.text in text
                or _text_covers(text, [b.text], min_frag=6)
            ),
            None,
        )
        if hit:
            ids.append(hit.beat_id)
        else:
            ids.append(text)  # 保留原文，由场记按语义判
    return ids


def _text_covers(target: str, corpus: list[str], *, min_frag: int = 8) -> bool:
    """目标关键叙述是否被某条节拍覆盖（子串或足够长的公共片段）。"""
    t = (target or "").strip()
    if not t:
        return True
    bodies = [(text or "").strip() for text in corpus if (text or "").strip()]
    if not bodies:
        return False
    for body in bodies:
        if t in body or body in t:
            return True
    # 长目标取多个窗口，避免只比前缀导致误杀改写后的分场
    window = max(min_frag, min(12, len(t)))
    step = max(1, window // 2)
    frags = [t[i : i + window] for i in range(0, max(1, len(t) - window + 1), step)]
    if len(t) <= window:
        frags = [t]
    for frag in frags:
        if len(frag) < min_frag:
            continue
        if any(frag in body for body in bodies):
            return True
    return False


def validate_scene_plan(
    plan: ChapterScenePlan,
    script: ChapterScript,
    choice: ScriptChoice,
    *,
    max_scenes: int,
) -> list[str]:
    """校验分场计划完整性与关键覆盖。

    返回问题列表；空列表表示通过。
    覆盖判定必须映射到具体节拍，不能用「must_land 非空」代替证明。
    """
    issues: list[str] = []
    if len(plan.cards) > max_scenes:
        issues.append(f"scenes_over_limit:{len(plan.cards)}>{max_scenes}")
    scene_ids = [c.scene_id for c in plan.cards]
    if len(scene_ids) != len(set(scene_ids)):
        issues.append("duplicate_scene_id")
    all_beats = beats_of_plan(plan)
    beat_ids = [b.beat_id for c in plan.cards for b in c.beats]
    if len(beat_ids) != len(set(beat_ids)):
        issues.append("duplicate_beat_id")
    if not all_beats:
        issues.append("no_beats")
    beat_texts = [b.text for b in all_beats]
    corpus = "；".join(beat_texts)

    turning = (script.turning_point or "").strip()
    cost = (script.cost or "").strip()
    end_change = (script.end_change or "").strip()
    if not _text_covers(turning, beat_texts) and turning not in corpus:
        issues.append(f"turning_point_not_covered:{turning[:24]}")
    if not _text_covers(cost, beat_texts) and cost not in corpus:
        issues.append(f"cost_not_covered:{cost[:24]}")
    if not _text_covers(end_change, beat_texts) and end_change not in corpus:
        issues.append(f"end_change_not_covered:{end_change[:24]}")

    # must_land：允许语义覆盖；全未映射才硬失败
    mapped = must_land_ids(plan, choice.must_land_beats)
    beat_id_set = {b.beat_id for b in all_beats}
    if choice.must_land_beats:
        matched = [m for m in mapped if m in beat_id_set]
        soft_covered = [
            t
            for t in choice.must_land_beats
            if (t or "").strip() and _text_covers(str(t), beat_texts, min_frag=6)
        ]
        unmatched = [m for m in mapped if m not in beat_id_set]
        if not matched and not soft_covered:
            issues.append(
                "must_land_beats_unmatched:"
                + ",".join(str(u)[:16] for u in unmatched[:4])
            )

    for card in plan.cards:
        if not card.beats:
            issues.append(f"empty_scene:{card.scene_id}")
    return issues


def structural_plan_issues(issues: list[str]) -> list[str]:
    """结构硬问题；覆盖/must_land 映射可在执笔与场记阶段补救，不算开拍阻断。"""
    soft_prefixes = (
        "must_land_beats_unmatched",
        "turning_point_not_covered",
        "cost_not_covered",
        "end_change_not_covered",
    )
    return [
        i
        for i in issues
        if not any(str(i).startswith(p) for p in soft_prefixes)
    ]


def invalidate_dependent_scenes(
    plan: ChapterScenePlan, from_scene_id: str
) -> list[str]:
    """重拍某场时，使其后的场景卡失效（返回失效 scene_id 列表）。

    当前协议 X 在原地局部重写，不走 retake 分叉；此函数为将来 retake 预留，
    尚未接入运行链——不要把它当成已有保障。
    """
    ids = [c.scene_id for c in plan.cards]
    if from_scene_id not in ids:
        return []
    idx = ids.index(from_scene_id)
    return ids[idx:]


def assemble_chapter(scenes: list[tuple[SceneCard, str]]) -> ChapterAssembled:
    """把各场正文按序合并成整章。"""
    parts: list[str] = []
    boundaries: list[int] = []
    offset = 0
    for _card, text in scenes:
        text = (text or "").strip()
        if not text:
            continue
        boundaries.append(offset)
        parts.append(text)
        offset += len(text) + 2
    content = "\n\n".join(parts)
    title = ""
    return ChapterAssembled(title=title, content=content, scene_boundaries=boundaries)
