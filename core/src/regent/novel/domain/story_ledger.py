"""长线控制账本：人物弧、能力规则、重复事件、故事段（Codex 方案 §五）。

与 domain.memory 的分工：

- ``memory`` 存 Canon 事实的确定性抽取（规则/承诺/误信/关系…），面向单章上下文召回。
- ``story_ledger`` 存**跨章创作控制状态**：人物选择造成的弧线变化、金手指规则与
  已消耗资源、已发生事件指纹（防重演）、当前故事段目标。

全部纯数据结构 + 确定性函数，不经过模型；模型只负责产出条目，账本负责查重与约束。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal


def _norm(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    return text.strip().lower()


def _fp(*parts: str) -> str:
    raw = "||".join(_norm(p) for p in parts if p)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 人物弧：记录"选择造成的变化"
# ---------------------------------------------------------------------------


@dataclass
class ArcBeat:
    """一次有代价的选择造成的人物变化。"""

    chapter_no: int
    prior_belief: str = ""
    shock: str = ""
    costly_choice: str = ""
    who_changed_view: str = ""
    next_pressure_option: str = ""

    def summary(self) -> str:
        bits = [
            f"此前相信：{self.prior_belief}" if self.prior_belief else "",
            f"冲击：{self.shock}" if self.shock else "",
            f"选择：{self.costly_choice}" if self.costly_choice else "",
            f"对方态度：{self.who_changed_view}" if self.who_changed_view else "",
            f"下次新选项：{self.next_pressure_option}" if self.next_pressure_option else "",
        ]
        return "；".join(b for b in bits if b)


@dataclass
class CharacterArc:
    name: str
    beats: list[ArcBeat] = field(default_factory=list)

    def latest(self) -> ArcBeat | None:
        return self.beats[-1] if self.beats else None

    def recent_summary(self, n: int = 3) -> str:
        return " | ".join(b.summary() for b in self.beats[-n:])


# ---------------------------------------------------------------------------
# 能力账本：固定机制，积累后果
# ---------------------------------------------------------------------------


@dataclass
class AbilityResource:
    """稳定资源 ID：提示文案可变化，判定只认 id。"""

    resource_id: str
    label: str = ""
    aliases: tuple[str, ...] = ()


@dataclass
class AbilityRule:
    """金手指规则。建立后不能因"需要更大代价"临时发明新机制。"""

    name: str
    mechanism: str = ""
    limits: str = ""
    inherent_cost: str = ""
    social_risk: str = ""
    upgrade_condition: str = ""
    resources: list[AbilityResource] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        lines = [
            f"【能力】{self.name}",
            f"机制：{self.mechanism}",
            f"限制：{self.limits}",
            f"固有代价：{self.inherent_cost}",
            f"社会后果：{self.social_risk}",
            f"升级条件：{self.upgrade_condition or '未设定；禁止临时发明'}",
        ]
        if self.resources:
            lines.append(
                "资源清单："
                + "；".join(
                    f"{r.resource_id}={r.label or r.resource_id}" for r in self.resources
                )
            )
        return "\n".join(lines)

    def resource_by_id(self, resource_id: str) -> AbilityResource | None:
        rid = (resource_id or "").strip()
        for r in self.resources:
            if r.resource_id == rid:
                return r
        return None

    def match_resource(self, text: str) -> AbilityResource | None:
        """从正文/事实中匹配资源：先匹配 id，再匹配 label/aliases。"""
        body = text or ""
        norm_body = _norm(body)
        for r in self.resources:
            if r.resource_id and r.resource_id in body:
                return r
            for alias in (r.label, *r.aliases):
                if alias and _norm(alias) and _norm(alias) in norm_body:
                    return r
        return None


@dataclass
class AbilityUse:
    chapter_no: int
    what: str
    cost_paid: str = ""
    resource_spent: str = ""  # 展示名；判定优先 resource_id
    resource_id: str = ""
    irreversible: bool = True


# 事实对资源的动作：use=再次动用；deplete=本次耗尽；mention=仅提及/否定
AbilityFactAction = Literal["use", "deplete", "mention", "none"]


def classify_ability_fact(
    fact: str,
    ability: AbilityLedger | None,
) -> tuple[AbilityFactAction, str]:
    """区分使用 / 耗尽 / 提及。返回 (动作, resource_id)。"""
    text = (fact or "").strip()
    if not text or ability is None:
        return "none", ""
    matched = ability.rule.match_resource(text)
    # 兼容旧账本：exhausted 里可能仍是展示名
    if matched is None:
        for ex in ability.exhausted:
            if ex and (_norm(ex) in _norm(text) or ex in text):
                return _ability_action_from_text(text), _resource_id_for_label(ability, ex)
        return "none", ""
    return _ability_action_from_text(text), matched.resource_id


def _resource_id_for_label(ability: AbilityLedger, label: str) -> str:
    hit = ability.rule.match_resource(label)
    if hit:
        return hit.resource_id
    return f"legacy:{_norm(label)[:24]}"


def _ability_action_from_text(text: str) -> AbilityFactAction:
    mention_ok = ("没有", "不再", "未再", "勿再", "不能再", "禁止", "已耗尽", "用尽", "未曾")
    if any(m in text for m in mention_ok):
        return "mention"
    # 先判再次动用：资源名常含「最后一次」，不能把它当成耗尽动词
    use_hints = ("再次", "动用", "使用", "消耗", "透支", "调用", "启用", "动用了", "抄了")
    deplete = ("耗尽", "用尽", "透支完", "不可再", "彻底用掉", "一次性耗尽")
    if any(m in text for m in deplete):
        return "deplete"
    if any(h in text for h in use_hints):
        return "use"
    return "mention"


@dataclass
class AbilityLedger:
    rule: AbilityRule
    uses: list[AbilityUse] = field(default_factory=list)
    # 已消耗且不可恢复的 resource_id（禁止下一章恢复成可再牺牲的筹码）
    exhausted: list[str] = field(default_factory=list)
    open_consequences: list[str] = field(default_factory=list)

    def assert_not_exhausted(self, resource: str) -> None:
        """硬阻断：资源已耗尽时抛错。resource 可为 resource_id 或展示名。"""
        rid = resource
        matched = self.rule.match_resource(resource)
        if matched:
            rid = matched.resource_id
        elif resource.startswith("legacy:"):
            rid = resource
        else:
            rid = _resource_id_for_label(self, resource)
        exhausted_ids = set(self.exhausted)
        # 兼容旧数据：exhausted 存的是展示名
        for ex in self.exhausted:
            hit = self.rule.match_resource(ex)
            if hit:
                exhausted_ids.add(hit.resource_id)
            else:
                exhausted_ids.add(f"legacy:{_norm(ex)[:24]}")
        if rid in exhausted_ids or _norm(resource) in {_norm(x) for x in self.exhausted}:
            raise ValueError(
                f"资源「{resource}」已消耗且不可恢复；禁止再次作为可牺牲筹码"
            )

    def is_exhausted(self, resource: str) -> bool:
        try:
            self.assert_not_exhausted(resource)
            return False
        except ValueError:
            return True

    def fact_reuses_exhausted(self, fact: str) -> str:
        """若事实再次动用或再次耗尽已耗尽资源，返回 resource_id；否则空串。"""
        action, rid = classify_ability_fact(fact, self)
        if action not in {"use", "deplete"} or not rid:
            return ""
        if self.is_exhausted(rid):
            return rid
        return ""

    def record(self, use: AbilityUse) -> None:
        rid = (use.resource_id or "").strip()
        if not rid and use.resource_spent:
            matched = self.rule.match_resource(use.resource_spent)
            rid = matched.resource_id if matched else f"legacy:{_norm(use.resource_spent)[:24]}"
            use.resource_id = rid
        if rid and use.irreversible:
            if rid not in self.exhausted:
                self.exhausted.append(rid)
        if use.cost_paid:
            self.open_consequences.append(
                f"第{use.chapter_no}章：{use.cost_paid}"
            )
        self.uses.append(use)

    def prompt_constraints(self) -> str:
        lines = [self.rule.as_prompt_block()]
        if self.exhausted:
            labels = []
            for rid in self.exhausted[-8:]:
                res = self.rule.resource_by_id(rid)
                labels.append(res.label if res and res.label else rid)
            lines.append("已耗尽不可恢复：" + "；".join(labels))
        if self.open_consequences:
            lines.append("未消化代价：" + "；".join(self.open_consequences[-6:]))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 重复事件检查：已发生事件不得重演
# ---------------------------------------------------------------------------


@dataclass
class EventFingerprint:
    chapter_no: int
    kind: str = ""  # 如：公开打脸 / 录音威胁 / 匿名举报
    actors: tuple[str, ...] = ()
    summary: str = ""
    fp: str = ""

    def __post_init__(self) -> None:
        if not self.fp:
            self.fp = _fp(self.kind, ",".join(sorted(self.actors)), self.summary[:40])


@dataclass
class EventLedger:
    events: list[EventFingerprint] = field(default_factory=list)

    def add(self, event: EventFingerprint) -> None:
        self.events.append(event)

    def find_similar(
        self,
        *,
        kind: str,
        actors: tuple[str, ...] = (),
        summary: str = "",
        window: int = 30,
    ) -> list[EventFingerprint]:
        """同 kind + 同主要演员 + 摘要高度重叠 → 视为重演。"""
        target = _fp(kind, ",".join(sorted(actors)), summary[:40])
        out: list[EventFingerprint] = []
        for ev in self.events:
            if ev.kind and kind and _norm(ev.kind) != _norm(kind):
                continue
            if actors:
                overlap = {a for a in actors if a in ev.actors}
                if not overlap:
                    continue
            if ev.fp == target:
                out.append(ev)
                continue
            # 摘要 4-gram 重叠率
            if summary and ev.summary:
                grams_a = {summary[i : i + 4] for i in range(max(0, len(summary) - 3))}
                grams_b = {
                    ev.summary[i : i + 4] for i in range(max(0, len(ev.summary) - 3))
                }
                if grams_a and grams_b:
                    ratio = len(grams_a & grams_b) / max(1, min(len(grams_a), len(grams_b)))
                    if ratio >= 0.55:
                        out.append(ev)
        return out[-window:]

    def as_prompt_block(self, limit: int = 12) -> str:
        if not self.events:
            return ""
        lines = ["【已发生事件，禁止原样重演】"]
        for ev in self.events[-limit:]:
            actors = "、".join(ev.actors) if ev.actors else ""
            lines.append(f"- 第{ev.chapter_no}章[{ev.kind}]{actors}：{ev.summary[:80]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 故事段：一段故事如何改变人物处境
# ---------------------------------------------------------------------------


@dataclass
class StorySegment:
    """3–8 章为一个故事段。段末若删去这几章人物处境不变，说明段落无效。"""

    segment_id: str
    goal: str = ""
    key_choice: str = ""
    irreversible_outcome: str = ""
    chapter_from: int = 0
    chapter_to: int = 0
    milestone: str = ""  # 远期里程碑，段内不展开
    status: Literal["active", "done", "abandoned"] = "active"

    def covers(self, chapter_no: int) -> bool:
        if self.status != "active":
            return False
        if self.chapter_from and chapter_no < self.chapter_from:
            return False
        if self.chapter_to and chapter_no > self.chapter_to:
            return False
        return True


@dataclass
class SegmentLedger:
    segments: list[StorySegment] = field(default_factory=list)

    def current(self, chapter_no: int) -> StorySegment | None:
        for seg in reversed(self.segments):
            if seg.covers(chapter_no):
                return seg
        return None

    def add(self, seg: StorySegment) -> None:
        self.segments.append(seg)

    def close(self, segment_id: str, *, status: str = "done") -> None:
        for seg in self.segments:
            if seg.segment_id == segment_id:
                seg.status = status  # type: ignore[assignment]
                return

    def as_prompt_block(self, chapter_no: int) -> str:
        seg = self.current(chapter_no)
        if not seg:
            return ""
        return (
            f"【当前故事段】{seg.segment_id}\n"
            f"段目标：{seg.goal}\n"
            f"关键选择：{seg.key_choice}\n"
            f"不可逆后果：{seg.irreversible_outcome}\n"
            f"远期里程碑（本段不展开）：{seg.milestone}"
        )


# ---------------------------------------------------------------------------
# 组合账本
# ---------------------------------------------------------------------------


@dataclass
class StoryLedger:
    """跨章创作控制状态。可 JSON 序列化，供连载脚本 resume。"""

    arcs: dict[str, CharacterArc] = field(default_factory=dict)
    ability: AbilityLedger | None = None
    events: EventLedger = field(default_factory=EventLedger)
    segments: SegmentLedger = field(default_factory=SegmentLedger)
    open_hooks: list[str] = field(default_factory=list)
    closed_hooks: list[str] = field(default_factory=list)

    # -- hooks ---------------------------------------------------------------
    def open_hook(self, hook: str) -> None:
        hook = (hook or "").strip()
        if hook and hook not in self.open_hooks:
            self.open_hooks.append(hook)

    def close_hook(self, hook: str) -> None:
        hook = (hook or "").strip()
        if not hook:
            return
        # 模糊关闭：前 8 字匹配也算
        key = _norm(hook)[:8]
        remain: list[str] = []
        for h in self.open_hooks:
            if key and key in _norm(h):
                self.closed_hooks.append(h)
            else:
                remain.append(h)
        self.open_hooks = remain

    def close_hooks_by(self, hooks: list[str]) -> None:
        for h in hooks:
            self.close_hook(h)

    # -- segments ------------------------------------------------------------
    def ensure_segment(self, chapter_no: int) -> StorySegment:
        """确保当前章被某个 active 故事段覆盖。

        未兑现 milestone/不可逆结果的占位段不得被自动标为 done 再开空框；
        应延长覆盖，迫使连载在既有段目标下推进。
        """
        seg = self.segments.current(chapter_no)
        if seg:
            return seg
        for s in self.segments.segments:
            if s.status != "active" or not s.chapter_to:
                continue
            if chapter_no <= s.chapter_to:
                continue
            if s.milestone or s.irreversible_outcome:
                s.status = "done"
            else:
                s.chapter_to = chapter_no + 7
                if not s.goal:
                    s.goal = "推进主线，让既有代价产生新的具体后果"
                return s
        prev = self.segments.segments[-1] if self.segments.segments else None
        goal = (
            f"承接「{prev.milestone or prev.goal}」之后：让既有代价产生新的具体后果"
            if prev
            else "推进主线，让人物处境发生可见变化"
        )
        new_seg = StorySegment(
            segment_id=f"seg_auto_{chapter_no}",
            goal=goal,
            key_choice="",
            irreversible_outcome="",
            chapter_from=chapter_no,
            chapter_to=chapter_no + 7,
            milestone="",
        )
        self.segments.add(new_seg)
        return new_seg

    # -- arcs ----------------------------------------------------------------
    def record_arc(self, name: str, beat: ArcBeat) -> None:
        arc = self.arcs.setdefault(name, CharacterArc(name=name))
        arc.beats.append(beat)

    def arc_prompt(self, name: str) -> str:
        arc = self.arcs.get(name)
        if not arc:
            return ""
        return f"【{name}人物弧近况】{arc.recent_summary()}"

    def ingest_chapter_outcome(
        self,
        *,
        chapter_no: int,
        facts: list[str] | None = None,
        hooks_opened: list[str] | None = None,
        hooks_closed: list[str] | None = None,
        character_shifts: list[str] | None = None,
        protagonist: str = "",
    ) -> None:
        """整章验收通过后，把最终事实/钩子/人物弧写入账本（仅正式提交，不写草稿）。

        若能力账本存在：再次动用已耗尽 resource_id 的事实会抛 ValueError；
        use/deplete 事实会写入 AbilityUse（稳定 resource_id）。
        """
        fact_texts = [str(f or "").strip() for f in (facts or []) if str(f or "").strip()]
        if self.ability:
            for text in fact_texts:
                rid = self.ability.fact_reuses_exhausted(text)
                if rid:
                    raise ValueError(
                        f"事实再次动用已耗尽资源 resource_id={rid}：{text[:80]}"
                    )
        for text in fact_texts:
            self.events.add(
                EventFingerprint(
                    chapter_no=int(chapter_no),
                    kind="committed_fact",
                    summary=text[:80],
                )
            )
        if self.ability:
            for text in fact_texts:
                action, rid = classify_ability_fact(text, self.ability)
                if action not in {"use", "deplete"} or not rid:
                    continue
                res = self.ability.rule.resource_by_id(rid)
                self.ability.record(
                    AbilityUse(
                        chapter_no=int(chapter_no),
                        what=text[:120],
                        resource_id=rid,
                        resource_spent=(res.label if res else rid),
                        irreversible=(action == "deplete" or bool(rid)),
                    )
                )
        for h in hooks_opened or []:
            self.open_hook(str(h))
        self.close_hooks_by([str(h) for h in (hooks_closed or [])])
        lead = (protagonist or "").strip()
        if lead:
            for shift in (character_shifts or [])[:3]:
                text = str(shift or "").strip()
                if not text:
                    continue
                self.record_arc(
                    lead,
                    ArcBeat(chapter_no=int(chapter_no), costly_choice=text[:120]),
                )
        self.ensure_segment(int(chapter_no))

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        ability: dict[str, Any] | None = None
        if self.ability:
            ability = {
                "rule": {
                    "name": self.ability.rule.name,
                    "mechanism": self.ability.rule.mechanism,
                    "limits": self.ability.rule.limits,
                    "inherent_cost": self.ability.rule.inherent_cost,
                    "social_risk": self.ability.rule.social_risk,
                    "upgrade_condition": self.ability.rule.upgrade_condition,
                    "resources": [
                        {
                            "resource_id": r.resource_id,
                            "label": r.label,
                            "aliases": list(r.aliases),
                        }
                        for r in self.ability.rule.resources
                    ],
                },
                "uses": [vars(u) for u in self.ability.uses],
                "exhausted": list(self.ability.exhausted),
                "open_consequences": list(self.ability.open_consequences),
            }
        return {
            "arcs": {
                name: {
                    "name": a.name,
                    "beats": [vars(b) for b in a.beats],
                }
                for name, a in self.arcs.items()
            },
            "ability": ability,
            "events": [vars(e) for e in self.events.events],
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "goal": s.goal,
                    "key_choice": s.key_choice,
                    "irreversible_outcome": s.irreversible_outcome,
                    "chapter_from": s.chapter_from,
                    "chapter_to": s.chapter_to,
                    "milestone": s.milestone,
                    "status": s.status,
                }
                for s in self.segments.segments
            ],
            "open_hooks": list(self.open_hooks),
            "closed_hooks": list(self.closed_hooks),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StoryLedger:
        data = data or {}
        led = cls()
        for name, a in (data.get("arcs") or {}).items():
            arc = CharacterArc(name=a.get("name") or name)
            for b in a.get("beats") or []:
                arc.beats.append(ArcBeat(**b))
            led.arcs[name] = arc
        ability = data.get("ability")
        if ability:
            rule_raw = dict(ability.get("rule") or {"name": "unknown"})
            resources_raw = list(rule_raw.pop("resources", None) or [])
            rule = AbilityRule(**rule_raw)
            rule.resources = [
                AbilityResource(
                    resource_id=str(r.get("resource_id") or ""),
                    label=str(r.get("label") or ""),
                    aliases=tuple(r.get("aliases") or ()),
                )
                for r in resources_raw
                if str(r.get("resource_id") or "").strip()
            ]
            uses = []
            for u in ability.get("uses") or []:
                payload = dict(u)
                uses.append(AbilityUse(**payload))
            led.ability = AbilityLedger(
                rule=rule,
                uses=uses,
                exhausted=list(ability.get("exhausted") or []),
                open_consequences=list(ability.get("open_consequences") or []),
            )
        for e in data.get("events") or []:
            ev = EventFingerprint(
                chapter_no=int(e.get("chapter_no") or 0),
                kind=str(e.get("kind") or ""),
                actors=tuple(e.get("actors") or ()),
                summary=str(e.get("summary") or ""),
                fp=str(e.get("fp") or ""),
            )
            led.events.add(ev)
        for s in data.get("segments") or []:
            led.segments.add(StorySegment(**s))
        led.open_hooks = list(data.get("open_hooks") or [])
        led.closed_hooks = list(data.get("closed_hooks") or [])
        return led

    def save_json(self, path: Any) -> None:
        from pathlib import Path

        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: Any) -> StoryLedger:
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            return cls()
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    # -- prompt assembly ------------------------------------------------------
    def prompt_block(
        self,
        *,
        chapter_no: int,
        focus_characters: tuple[str, ...] = (),
        max_events: int = 10,
    ) -> str:
        parts: list[str] = []
        seg = self.segments.as_prompt_block(chapter_no)
        if seg:
            parts.append(seg)
        if self.ability:
            parts.append(self.ability.prompt_constraints())
        for name in focus_characters or tuple(self.arcs.keys()):
            block = self.arc_prompt(name)
            if block:
                parts.append(block)
        ev = self.events.as_prompt_block(limit=max_events)
        if ev:
            parts.append(ev)
        if self.open_hooks:
            parts.append("【未解钩子】" + "；".join(self.open_hooks[-8:]))
        if self.closed_hooks:
            parts.append("【已关闭钩子，不得再吊】" + "；".join(self.closed_hooks[-4:]))
        return "\n".join(parts)


def empty_story_ledger() -> StoryLedger:
    """空账本：连载实验默认。规则/资源仅来自选定剧本首登记者，不在此预置。"""
    return StoryLedger()


def register_ability_from_script(
    ledger: StoryLedger,
    script: dict[str, Any],
    *,
    finger_name: str = "文抄公",
) -> bool:
    """选定剧本首次确立金手指正史。已有 ability 时不覆盖。"""
    if ledger.ability is not None:
        return False
    mechanism = str(script.get("power_mechanism") or "").strip()
    limits = str(script.get("power_limits") or "").strip()
    if not mechanism:
        mechanism = str(script.get("power_payoff") or "").strip()
    if not mechanism and not limits:
        return False
    cost = str(script.get("cost") or "").strip()
    ledger.ability = AbilityLedger(
        rule=AbilityRule(
            name=finger_name,
            mechanism=mechanism[:600],
            limits=(limits or "以选定剧本为准，续章不得擅自改机制")[:600],
            inherent_cost=cost[:400],
            social_risk="",
            upgrade_condition="",
            resources=[],
        )
    )
    return True


def gender_bend_ledger_test_preset() -> StoryLedger:
    """单元测试用：带缓存资源等预设。生产连载请用 empty_story_ledger()。"""
    led = StoryLedger()
    led.ability = AbilityLedger(
        rule=AbilityRule(
            name="文抄公",
            mechanism="记忆中有未播爆款恋综与同期文娱黑料时间线，可复现未来名场面与打脸节点",
            limits="只能调用脑海已有成片/黑料时间线；不能实时预知未写入缓存的结果；每次调用须落到一条具体情报",
            inherent_cost=(
                "使用代价由选定剧本定义并记入账本；须在文娱圈常理下可信。"
                "禁止把「被骂剧本女」记为主代价。"
            ),
            social_risk="异常表现会招致制作组/对家追问情报来源",
            upgrade_condition="",
            resources=[
                AbilityResource(
                    resource_id="future_cache",
                    label="未来情报缓存",
                    aliases=("缓存", "未播缓存", "未来缓存", "最后一次缓存", "最后缓存", "缓存额度"),
                ),
                AbilityResource(
                    resource_id="insider_leverage",
                    label="内鬼嫌疑把柄",
                    aliases=("内鬼", "未播台本嫌疑", "内鬼把柄", "台本内鬼"),
                ),
                AbilityResource(
                    resource_id="anonymous_leak_slot",
                    label="匿名爆料窗口",
                    aliases=("爆料窗口", "匿名举报位"),
                ),
            ],
        )
    )
    led.segments.add(
        StorySegment(
            segment_id="seg1_恋综开机",
            goal="开机录制周：建立女主公开场域存在感，并完成第一次文抄公兑现",
            key_choice="本章由选定剧本定义",
            irreversible_outcome="本章验收通过后的事实写入账本",
            chapter_from=1,
            chapter_to=8,
            milestone="拿到首波镜头位，并留下圈内可信的未解压力",
        )
    )
    led.segments.add(
        StorySegment(
            segment_id="seg2_圈内攻防",
            goal="通告/剪辑/资方层面扩大或回收第一次兑现的后果",
            key_choice="本章由选定剧本定义",
            irreversible_outcome="至少一条把柄或资源变化不可逆",
            chapter_from=9,
            chapter_to=16,
            milestone="从空降新人变成有议题的危险嘉宾",
        )
    )
    led.segments.add(
        StorySegment(
            segment_id="seg3_身份危机",
            goal="性转前身份风险上升，舞台与旧关系不可兼得时做选择",
            key_choice="本章由选定剧本定义",
            irreversible_outcome="至少一段旧关系或公开形象不可逆改变",
            chapter_from=17,
            chapter_to=24,
            milestone="公开形象与私下身份开始分裂",
        )
    )
    led.open_hook("开机录制周尚未收束")
    return led


def default_gender_bend_ledger() -> StoryLedger:
    """兼容旧测试导入。生产连载请用 empty_story_ledger()。"""
    return gender_bend_ledger_test_preset()
