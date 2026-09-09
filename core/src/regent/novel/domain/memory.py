"""长期创作记忆：稳定规则、人物弧线、承诺与伏笔、关系变化（Plan §4 R3）。

设计约束（与 G-02 / G-03 / G-07 同源）：

- **抽取必须是确定的**：同一批 Canon 事实永远得到同一批记忆条目，不经过模型，
  否则「为什么这条规则被记住」无法举证。
- **召回必须按需**：上下文预算有限，全量塞入等于没有记忆；未兑现的承诺不得被
  裁剪掉——伏笔回收的失败是不可逆的阅读体验损失。
- **改意只失效不修改**：用户改意后旧条目标 ``invalidated`` 保留，事实链不动，
  避免「旧版上下文污染新版」。
- **重演取最小子图**：只重演依赖被改动节点的下游；依赖信息不完整时**保守**
  退回重做当前章后续场景，不假装能算出子图。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

MemoryKind = Literal[
    "rule", "character_arc", "promise", "relation",
    "belief", "reader_knowledge", "director_note",
]
MemoryState = Literal["OPEN", "RESOLVED", "ABANDONED"]

# 六视图（技术方案 §3.2）←→ 存储 kind 的对应：
#   世界事实 = rule；人物状态 = character_arc + relation；人物认知 = belief；
#   读者认知 = reader_knowledge；承诺与伏笔 = promise；导演记忆 = director_note。
MEMORY_KINDS: tuple[str, ...] = (
    "rule", "character_arc", "promise", "relation",
    "belief", "reader_knowledge", "director_note",
)
MEMORY_STATES: tuple[str, ...] = ("OPEN", "RESOLVED", "ABANDONED")

# 显式标注的别名：Canon 事实可以带 memory_kind / kind，写错就不记，不猜。
_KIND_ALIASES: dict[str, str] = {
    "rule": "rule",
    "world_rule": "rule",
    "stable_rule": "rule",
    "stable": "rule",
    "world_fact": "rule",
    "设定": "rule",
    "character_arc": "character_arc",
    "arc": "character_arc",
    "arc_stage": "character_arc",
    "character_state": "character_arc",
    "人物弧线": "character_arc",
    "promise": "promise",
    "foreshadow": "promise",
    "伏笔": "promise",
    "承诺": "promise",
    "relation": "relation",
    "relationship": "relation",
    "关系": "relation",
    "belief": "belief",
    "misbelief": "belief",
    "character_belief": "belief",
    "误信": "belief",
    "reader_knowledge": "reader_knowledge",
    "reader_cognition": "reader_knowledge",
    "revealed": "reader_knowledge",
    "读者认知": "reader_knowledge",
    "director_note": "director_note",
    "director_memory": "director_note",
    "导演记忆": "director_note",
}

_DEFAULT_STATE: dict[str, str] = {
    "rule": "RESOLVED",       # 稳定规则一旦成立就生效，不需要「兑现」
    "character_arc": "OPEN",  # 弧线持续推进
    "promise": "OPEN",        # 未兑现的承诺必须一直被记住
    "relation": "RESOLVED",
    "belief": "OPEN",         # 误信在被纠正之前一直驱动行为
    "reader_knowledge": "RESOLVED",
    "director_note": "OPEN",  # 待解决问题未解决前一直挂着
}

# 「已确认没有上游依赖」的标记边的上游键。独立必须被显式登记：否则没有入边的
# 条目既可能是独立，也可能是漏记了依赖，两者在存储里无法区分（B-02）。
INDEPENDENT_MARKER = "__independent__"

# 一条承诺一个条目：同一人物先承诺归还钥匙、后承诺复仇，是两件事。用
# (kind, subject) 当键会让第二条覆盖第一条——伏笔就丢在覆盖里了（B-01）。
APPEND_ONLY_KINDS: frozenset[str] = frozenset({"promise", "belief", "director_note"})

# 分类依据：只记「凭什么这么分」，供举证。误判不可怕，说不清依据才可怕。
BASIS_EXPLICIT = "explicit_kind"
BASIS_MARKER = "explicit_marker"
BASIS_PROMISE_SIGNAL = "promise_signal"
BASIS_BELIEF_SIGNAL = "belief_signal"
BASIS_READER_SIGNAL = "reader_signal"
BASIS_DIRECTOR_SIGNAL = "director_signal"
BASIS_RELATION_SIGNAL = "relation_signal"
BASIS_CO_OCCURRENCE = "co_occurrence"
BASIS_SINGLE_CAST = "single_cast_member"

# 共现只是**结构性推断**：两个人同场不等于他们的关系变了。推断出来的关系必须
# 与显式关系变化区分开，否则「有边/有条目」会被当成「关系已变」（B-01）。
CONFIDENCE_BY_BASIS: dict[str, str] = {
    BASIS_EXPLICIT: "high",
    BASIS_MARKER: "high",
    BASIS_PROMISE_SIGNAL: "medium",
    BASIS_BELIEF_SIGNAL: "medium",
    BASIS_READER_SIGNAL: "medium",
    BASIS_DIRECTOR_SIGNAL: "high",
    BASIS_RELATION_SIGNAL: "high",
    BASIS_CO_OCCURRENCE: "medium",
    BASIS_SINGLE_CAST: "medium",
}

_PROMISE_MARKERS: tuple[str, ...] = (
    "承诺", "许诺", "答应", "发誓", "起誓", "立誓", "誓言", "约定",
    "保证", "必将", "一定会", "定要", "来日必", "有朝一日", "他日必",
    "埋下伏笔", "伏笔", "日后", "总有一天",
)
_BELIEF_MARKERS: tuple[str, ...] = (
    "以为", "误以为", "误认为", "误信", "不知道", "并不知", "毫不知情",
    "相信", "深信", "误判", "错认", "误", "以为自己",
)
_RELATION_MARKERS: tuple[str, ...] = (
    "结盟", "反目", "决裂", "和好", "结为", "拜师", "收徒", "师徒",
    "背叛", "投靠", "相认", "断交", "恩断义绝", "化敌为友", "联姻", "定亲",
)
_READER_MARKERS: tuple[str, ...] = (
    "读者", "读者知道", "读者尚不", "未向读者", "旁白未揭示", "刻意保留", "未揭示",
)
_DIRECTOR_MARKERS: tuple[str, ...] = (
    "待解决", "尚待", "下次避免", "失败原因", "表现有效", "风格约束", "待办", "遗留",
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryItem:
    """一条长期记忆。``key`` 在同一作品分支内唯一，决定幂等与失效范围。"""

    key: str
    kind: str
    subject: str
    content: str
    entities: tuple[str, ...] = ()
    state: str = "OPEN"
    confidence: str = "high"
    source_chapter_no: int = 0
    source_hash: str = ""
    invalidated: bool = False
    # 分类依据与兑现章号：都不参与存储列的唯一性，但都进 manifest——
    # 「凭什么这么分类」「在哪一章兑现的」必须可举证（B-01）。
    basis: str = ""
    resolved_chapter_no: int = 0
    # 创作输入**显式声明**「这条没有上游」。它是独立性唯一的正面证据来源；
    # 纯运行时字段（不入库、不进 as_payload），只在同一次建边时使用——
    # 独立性必须被声明，不能由「实体没交集」反推（C-04）。
    declared_independent: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "subject": self.subject,
            "content": self.content,
            "entities": list(self.entities),
            "state": self.state,
            "confidence": self.confidence,
            "source_chapter_no": self.source_chapter_no,
            "source_hash": self.source_hash,
            "invalidated": self.invalidated,
            "basis": self.basis,
            "resolved_chapter_no": self.resolved_chapter_no,
        }

    @property
    def view(self) -> str:
        """技术方案 §3.2 的六视图名。存储 kind 与视图不是一一对应的。"""
        return VIEW_OF_KIND.get(self.kind, "world_fact")


@dataclass(frozen=True)
class ReplayPlan:
    """重演计划。``complete`` 为 False 表示依赖信息不完整，调用方必须保守重做。

    ``independent`` / ``unknown`` 是**覆盖依据**，不是装饰：有边只说明记录了依赖，
    不说明记录了全部依赖。一条边都没有的条目既可能是真的独立，也可能是漏记了
    依赖——只有显式标了 independent 才能按独立处理，否则必须算作 unknown。
    """

    subjects: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()
    chapters: tuple[int, ...] = ()
    complete: bool = True
    reason: str = ""
    independent: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "subjects": list(self.subjects),
            "keys": list(self.keys),
            "chapters": list(self.chapters),
            "complete": self.complete,
            "reason": self.reason,
            "independent": list(self.independent),
            "unknown": list(self.unknown),
        }


def item_key(kind: str, subject: str, content: str = "") -> str:
    """记忆条目的键。

    ``APPEND_ONLY_KINDS`` 带上内容指纹：承诺、误信、导演待办天然是多条并存的，
    按 (kind, subject) 归一会互相覆盖——「甲承诺归还钥匙」被「甲立誓复仇」盖掉，
    伏笔就丢在覆盖里，且丢得悄无声息（B-01）。

    其余类别保持 (kind, subject)：世界规则被改写时应当替换旧值，留着两条互相
    矛盾的设定只会让下一章随机挑一条。
    """
    base = f"{kind}:{subject.strip()}"
    if kind in APPEND_ONLY_KINDS and content.strip():
        return f"{base}#{digest(content.strip())[:8]}"
    return base


def _cast_names(fact: dict[str, Any], cast: Sequence[str]) -> list[str]:
    """事实里出现的人物名（按字典序，避免事实内部顺序影响结果）。"""
    if not cast:
        return []
    known = {str(name).strip() for name in cast if str(name).strip()}
    pool: list[str] = []
    for key in ("known_by", "entities", "participants", "subjects"):
        value = fact.get(key)
        if isinstance(value, (list, tuple)):
            pool.extend(str(part).strip() for part in value)
        elif isinstance(value, str) and value.strip():
            pool.append(value.strip())
    return sorted({name for name in pool if name in known})


def _has_marker(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


def classify_with_basis(
    fact: dict[str, Any], cast: Sequence[str] = ()
) -> tuple[str | None, str]:
    """判定一条 Canon 事实属于哪类记忆，并给出**分类依据**；判定不了就 (None, "")。

    宁可漏记也不误记：误记会把噪声写进「稳定规则」，之后每一章都会被它污染。
    但只给类别不给依据同样不行——「甲承诺明日归还钥匙」被记成人物弧线时，如果
    没有依据可查，就无法区分这是判定错误还是故意归类（B-01）。

    顺序即可信度：显式标注 > 显式字段 > 内容信号 > 人物结构。**承诺信号必须排在
    人物结构之前**：承诺通常只提到一个人，按结构判就是弧线，于是每一条承诺都会
    被写成人物弧线并在下一条承诺到来时被覆盖。
    """
    if not isinstance(fact, dict):
        return None, ""
    raw = fact.get("memory_kind") or fact.get("kind") or ""
    if isinstance(raw, str) and raw.strip():
        kind = _KIND_ALIASES.get(raw.strip().lower())
        if kind is not None:
            return kind, BASIS_EXPLICIT
        # 写错就不记，不猜：猜出来的类别会静默写进之后每一章。
        return None, ""
    for marker, kind in (
        ("world_rule", "rule"),
        ("stable_rule", "rule"),
        ("arc_stage", "character_arc"),
        ("character_change", "character_arc"),
        ("promise", "promise"),
        ("foreshadow", "promise"),
        ("relation_between", "relation"),
        ("misbelief", "belief"),
        ("belief", "belief"),
        ("reader_knowledge", "reader_knowledge"),
        ("director_note", "director_note"),
    ):
        if fact.get(marker):
            return kind, BASIS_MARKER

    statement = str(fact.get("statement") or fact.get("content") or "")
    if _has_marker(statement, _PROMISE_MARKERS) or fact.get("resolves") or fact.get(
        "payoff"
    ):
        return "promise", BASIS_PROMISE_SIGNAL
    # 顺序按**信号的专指度**排：越专指越先。
    # 「读者尚不知道…」里也有「不知道」，若让泛化的 belief 信号先命中，关于叙事
    # 披露的表述就会被记成人物的误信——人物认知与读者认知在这里必须分开（B-01）。
    if _has_marker(statement, _READER_MARKERS):
        return "reader_knowledge", BASIS_READER_SIGNAL
    if _has_marker(statement, _DIRECTOR_MARKERS):
        return "director_note", BASIS_DIRECTOR_SIGNAL
    if _has_marker(statement, _BELIEF_MARKERS):
        return "belief", BASIS_BELIEF_SIGNAL
    if _has_marker(statement, _RELATION_MARKERS):
        return "relation", BASIS_RELATION_SIGNAL

    names = _cast_names(fact, cast)
    if len(names) >= 2:
        # 共现只是结构推断：同场不等于关系变化，因此置信度低于显式关系信号。
        return "relation", BASIS_CO_OCCURRENCE
    if len(names) == 1:
        return "character_arc", BASIS_SINGLE_CAST
    return None, ""


def classify(fact: dict[str, Any], cast: Sequence[str] = ()) -> str | None:
    """判定一条 Canon 事实属于哪类记忆；判定不了就返回 None（不记）。"""
    kind, _basis = classify_with_basis(fact, cast)
    return kind


def _subject_of(fact: dict[str, Any], kind: str, cast: Sequence[str] = ()) -> str:
    for key in ("subject", "name", "title"):
        value = fact.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    between = fact.get("relation_between")
    if isinstance(between, (list, tuple)) and between:
        return "↔".join(str(part) for part in between)
    names = _cast_names(fact, cast)
    if kind == "relation" and len(names) >= 2:
        return "↔".join(names[:2])
    if kind == "reader_knowledge":
        # 读者认知的对象通常是**信息或物件**（「钥匙的来处」），不是人物。挂在
        # 人物名下会把「甲不知道…」和「读者不知道…」混成同一条（B-01）。
        text = str(fact.get("statement") or fact.get("reader_knowledge") or "")
        return text[:40].strip()
    # 承诺/误信/导演记忆挂在人身上：主题取在册人物，取不到才退回文本摘要。
    # 顺序错了会取到空主题，于是「甲承诺明日归还钥匙」因为没主题被整条丢掉——
    # 正是承诺记不进去的那条路径（B-01）。
    if kind in ("character_arc", "promise", "belief", "director_note") and names:
        return names[0]
    entities = fact.get("entities")
    if isinstance(entities, (list, tuple)) and entities:
        return str(entities[0])
    if kind in ("promise", "belief", "director_note"):
        text = str(
            fact.get("promise")
            or fact.get("foreshadow")
            or fact.get("belief")
            or fact.get("director_note")
            or fact.get("statement")
            or ""
        )
        return text[:40].strip()
    return ""


def _content_of(fact: dict[str, Any], kind: str) -> str:
    # statement 是正式事实的内容字段：不读它，正常章节一条记忆都写不进去。
    for key in ("statement", "fact", "content", "text", "summary", "description"):
        value = fact.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("world_rule", "stable_rule", "arc_stage", "promise", "foreshadow"):
        value = fact.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_items(
    facts: Sequence[dict[str, Any]],
    *,
    chapter_no: int,
    source_hash: str = "",
    cast: Sequence[str] = (),
) -> list[MemoryItem]:
    """从一批 Canon 事实里确定性地抽取长期记忆。

    ``cast`` 是在册人物名：正式事实不带分类标记时，人物共现是唯一可信的
    结构信号，缺了它就只能靠显式标注，正常章节会一条都写不进去。
    """
    items: dict[str, MemoryItem] = {}
    for fact in facts:
        kind, basis = classify_with_basis(fact, cast)
        if kind is None:
            continue
        subject = _subject_of(fact, kind, cast)
        content = _content_of(fact, kind)
        if not subject or not content:
            continue
        entities = fact.get("entities")
        entities = (
            tuple(sorted({str(e) for e in entities if str(e).strip()}))
            if isinstance(entities, (list, tuple))
            else ()
        )
        state = str(fact.get("state") or _DEFAULT_STATE[kind]).upper()
        if state not in MEMORY_STATES:
            state = _DEFAULT_STATE[kind]
        key = item_key(kind, subject, content)
        confidence = str(fact.get("confidence") or "").strip().lower()
        if confidence not in ("high", "medium", "low"):
            # 没标就用依据推：共现/内容信号推断出来的关系不能冒充 high，
            # 否则「同场」与「反目」在召回里权重相同（B-01）。
            confidence = CONFIDENCE_BY_BASIS.get(basis, "high")
        # 同键后写覆盖：同一次提交里后来的事实更完整。承诺/误信/导演记忆带内容
        # 指纹，因此它们不会被同类覆盖。
        items[key] = MemoryItem(
            key=key,
            kind=kind,
            subject=subject,
            content=content,
            entities=entities,
            state=state,
            confidence=confidence,
            source_chapter_no=int(chapter_no),
            source_hash=source_hash,
            basis=basis,
            # 只有创作输入显式声明才算「有独立性证据」（C-04）。
            declared_independent=bool(fact.get("independent")),
        )
    return [items[key] for key in sorted(items)]


def resolve_items(
    items: Iterable[MemoryItem],
    payoff_facts: Sequence[dict[str, Any]],
    *,
    chapter_no: int,
) -> list[MemoryItem]:
    """兑现：新事实里出现 ``resolves`` / ``payoff`` 指向的主题即标记为 RESOLVED。

    只改状态不改内容：事实链保持 append-only，兑现本身也是一条可举证的变更。
    """
    targets: set[str] = set()
    for fact in payoff_facts:
        if not isinstance(fact, dict):
            continue
        for key in ("resolves", "payoff", "closes"):
            value = fact.get(key)
            if isinstance(value, str) and value.strip():
                targets.add(value.strip())
            elif isinstance(value, (list, tuple)):
                targets.update(str(v).strip() for v in value if str(v).strip())
    # 按主题兑现会把同一个人名下**所有**未兑现承诺一起关掉——「甲」还了钥匙，
    # 不代表他三年前的复仇誓也还了。因此按 subject 命中只在「该主题下仅此一条
    # 未兑现」时成立；有歧义就必须按具体承诺（key 或承诺内容）精确命中（C-03）。
    open_counts: dict[str, int] = {}
    for item in items:
        if not item.invalidated and item.state == "OPEN":
            open_counts[item.subject] = open_counts.get(item.subject, 0) + 1
    resolved: list[MemoryItem] = []
    for item in items:
        if item.invalidated or item.state != "OPEN":
            resolved.append(item)
            continue
        hit = item.key in targets or str(item.content or "").strip() in targets
        if not hit and item.subject in targets:
            hit = open_counts.get(item.subject, 0) == 1
        if hit:
            resolved.append(
                MemoryItem(
                    key=item.key, kind=item.kind, subject=item.subject,
                    content=item.content, entities=item.entities, state="RESOLVED",
                    confidence=item.confidence, source_chapter_no=item.source_chapter_no,
                    source_hash=item.source_hash, basis=item.basis,
                    # 兑现章号与种下章号分开存：「已经兑现」和「从没被记住」不能
                    # 在存储里长得一样，否则跨章保留未兑现承诺无法验证（B-01）。
                    resolved_chapter_no=int(chapter_no),
                )
            )
        else:
            resolved.append(item)
    return resolved


# ---------------------------------------------------------------------------
# 六视图与可见性边界（技术方案 §3.2 / B-01）
# ---------------------------------------------------------------------------

VIEW_OF_KIND: dict[str, str] = {
    "rule": "world_fact",
    "character_arc": "character_state",
    "relation": "character_state",
    "belief": "character_belief",
    "reader_knowledge": "reader_cognition",
    "promise": "promise_and_foreshadow",
    "director_note": "director_memory",
}

VIEWS: tuple[str, ...] = (
    "world_fact",
    "character_state",
    "character_belief",
    "reader_cognition",
    "promise_and_foreshadow",
    "director_memory",
)

# 受众：谁可以看到这类记忆。
# - character：人物自己的上下文。**人物认知可见，读者认知不可见**——人物不知道
#   读者知道了什么；反过来把读者认知喂给人物，等于让人物按剧本知情。
# - reader/narrator：进入正文或供读者检索的投影。**导演记忆永不进正文**：那里面
#   记的是失败原因与待解决问题，写进正文就是创作事故。
VISIBLE_TO: dict[str, tuple[str, ...]] = {
    "director": VIEWS,
    "character": ("world_fact", "character_state", "character_belief",
                  "promise_and_foreshadow"),
    "narrator": ("world_fact", "character_state", "reader_cognition",
                 "promise_and_foreshadow"),
    "reader": ("world_fact", "character_state", "reader_cognition"),
}


def visible_views(audience: str) -> tuple[str, ...]:
    """某类受众可见的视图。未知受众一律按最严格处理（只看世界事实）。"""
    return VISIBLE_TO.get(audience, ("world_fact",))


def project_for(
    items: Iterable[MemoryItem], audience: str, *, view: str = ""
) -> tuple[MemoryItem, ...]:
    """按受众裁剪记忆。导演记忆对非导演受众一律不可见。

    ``view`` 非空时只取该视图。误信对人物自己可见（它驱动行为），但对读者不可见
    ——读者看到的是「人物以为」，那是叙事内容，不是记忆投影。
    """
    allowed = visible_views(audience)
    picked = [
        item
        for item in items
        if not item.invalidated
        and item.view in allowed
        and (not view or item.view == view)
    ]
    return tuple(sorted(picked, key=lambda i: (i.kind, i.subject, i.key)))


def open_promises(items: Iterable[MemoryItem]) -> tuple[MemoryItem, ...]:
    """尚未兑现的承诺与伏笔：它们永不因召回限额被裁掉。"""
    return tuple(
        sorted(
            (
                item
                for item in items
                if not item.invalidated
                and item.kind == "promise"
                and item.state == "OPEN"
            ),
            key=lambda i: (i.source_chapter_no, i.key),
        )
    )


def recall_score(
    item: MemoryItem,
    *,
    chapter_no: int,
    kinds: Sequence[str] = (),
    entities: Sequence[str] = (),
) -> float:
    """召回打分。同源输入必得同分，排序键兜底保证确定性（G-02）。"""
    if item.invalidated:
        return -1.0
    score = 0.0
    if kinds and item.kind in kinds:
        score += 3.0
    wanted = {str(e) for e in entities if str(e)}
    if wanted:
        score += 2.0 * len(wanted & set(item.entities))
    if item.state == "OPEN":
        # 未兑现的承诺：裁掉它就等于丢掉伏笔，权重必须压过新鲜度
        score += 1.5
    if item.kind == "rule":
        score += 1.0  # 稳定规则长期有效
    age = max(0, int(chapter_no) - int(item.source_chapter_no))
    score += 1.0 / (1.0 + age / 10.0)
    return score


def recall(
    items: Iterable[MemoryItem],
    *,
    chapter_no: int,
    kinds: Sequence[str] = (),
    entities: Sequence[str] = (),
    limit: int = 24,
) -> list[MemoryItem]:
    """按需召回。未兑现的承诺永不因限额被裁掉。"""
    scored = [
        (recall_score(item, chapter_no=chapter_no, kinds=kinds, entities=entities), item)
        for item in items
    ]
    scored = [(s, i) for s, i in scored if s >= 0]
    # 排序键兜底：同分时按 kind/subject/key，字典序与调用次序不影响结果。
    scored.sort(key=lambda pair: (-pair[0], pair[1].kind, pair[1].subject, pair[1].key))
    picked = [item for _, item in scored[:limit]]
    keys = {item.key for item in picked}
    for _score, item in scored:
        if item.state == "OPEN" and item.key not in keys:
            picked.append(item)
            keys.add(item.key)
    picked.sort(key=lambda i: (i.kind, i.subject, i.key))
    return picked


def _resolve_changed_keys(
    items: Sequence[MemoryItem], changed: Sequence[str]
) -> set[str]:
    """把「被改动的主题」解析成受影响条目键。

    调用方拿到的是**主题**（节点标题、人物名），不是内部键——要求调用方拼内部键，
    等于要求它知道记忆是怎么被抽取的，任一侧改口径都会静默失效。

    同一个主题可能挂着多条记忆（一个标题既可以是承诺也可以是弧线）；以它为参与者
    的关系条目同样受影响：改了这个人物，他与别人的关系也不再可信。
    """
    wanted = {str(part).strip() for part in changed if str(part).strip()}
    if not wanted:
        return set()
    explicit = {part for part in wanted if ":" in part}
    subjects = wanted - explicit
    keys: set[str] = set()
    for item in items:
        if item.key in explicit or item.subject in subjects or (
            subjects & set(item.entities)
        ):
            keys.add(item.key)
    return keys


def coverage_of(
    items: Sequence[MemoryItem],
    edges: Sequence[tuple[str, str]],
    independent: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """把条目分成「依赖已知」与「依赖未知」两类（B-02）。

    **有边不等于图完整。** 一个条目没有入边，可能是它真的没有上游（独立），
    也可能是漏记了它的上游。两者处置完全相反：前者可以精确重演，后者必须保守
    重做。没有 explicit 的 independent 标记时，一律按未知处理。
    """
    known = {item.key for item in items}
    has_incoming = {down for _up, down in edges if down in known}
    marked = {key for key in independent if key in known}
    covered = {key for key in known if key in has_incoming or key in marked}
    unknown = tuple(sorted(known - covered))
    return tuple(sorted(covered)), unknown


def replay_subgraph(
    items: Sequence[MemoryItem],
    edges: Sequence[tuple[str, str]],
    *,
    changed: Sequence[str],
    independent: Sequence[str] = (),
) -> ReplayPlan:
    """最小依赖子图重演：从被改动的主题出发，只取可达的下游。

    ``edges`` 为 ``(upstream, downstream)`` 依赖边，``independent`` 是已显式确认
    没有上游的条目键。**依赖不完整时不能假装算出子图**——返回 ``complete=False``，
    由调用方保守重做当前章之后的场景。
    """
    changed_keys = _resolve_changed_keys(items, changed)
    if not changed_keys:
        return ReplayPlan(complete=False, reason="没有给出被改动的主题")
    known = {item.key for item in items}
    downstream: dict[str, set[str]] = {key: set() for key in known}
    missing = False
    for upstream, downstream_key in edges:
        if upstream not in known or downstream_key not in known:
            missing = True
            continue
        downstream[upstream].add(downstream_key)

    seen: set[str] = set()
    stack = sorted(changed_keys)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(sorted(downstream.get(current, set()) - seen))

    affected = [item for item in items if item.key in seen]
    chapters = sorted({int(i.source_chapter_no) for i in affected})
    keys = tuple(sorted(item.key for item in affected))
    subjects = tuple(sorted(i.subject for i in affected))
    covered, unknown = coverage_of(items, edges, independent)
    if missing:
        # 有边指向未知条目：图不完整，结果不可信。
        return ReplayPlan(
            subjects=subjects,
            keys=keys,
            chapters=tuple(chapters),
            independent=covered,
            unknown=unknown,
            complete=False,
            reason="依赖边不完整：无法确定最小子图，应保守重做当前章后续场景",
        )
    if unknown:
        # 有条目从没被登记过依赖信息：它独立还是漏记了，系统不知道。
        return ReplayPlan(
            subjects=subjects,
            keys=keys,
            chapters=tuple(chapters),
            independent=covered,
            unknown=unknown,
            complete=False,
            reason=(
                f"有 {len(unknown)} 条记忆没有依赖覆盖记录：无法区分独立与漏记，"
                "应保守重做当前章后续场景"
            ),
        )
    return ReplayPlan(
        subjects=subjects,
        keys=keys,
        chapters=tuple(chapters),
        independent=covered,
    )


@dataclass(frozen=True)
class MemoryBundle:
    """一次召回的结果与可复现凭证。"""

    items: tuple[MemoryItem, ...] = ()
    limit: int = 24
    chapter_no: int = 0
    requested_kinds: tuple[str, ...] = ()
    requested_entities: tuple[str, ...] = field(default=())

    @property
    def source_hash(self) -> str:
        return digest([item.as_payload() for item in self.items])

    def as_payload(self) -> list[dict[str, Any]]:
        return [item.as_payload() for item in self.items]
