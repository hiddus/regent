"""状态账本：什么能入账、什么只能留证、什么必须隔离。

这一层的存在理由只有一个：**账本会被用来判死后面的章节**。所以每一条写入都要先
回答一个问题——这条的「值」有没有**机械依据**？

- 有：引文里真的出现了这个值（持有人名字就在那句里）→ `verified`，可以投影成当前态。
- 没有：引文真实，但值是我们读出来的（「信被烧了」推出 lifecycle=destroyed）→ `candidate`，
  只留证，永不投影。

「引文存在」与「值被引文支持」是两件事。前者只证明模型会抄原文——它总能在这章三千字里
找到一句真实存在的话当证据；后者才能证明「这个值就是这句说的」。本轮复现过的反例：
正文与引文都是「陈甲把铜钥收进袖中。」，模型给出 holder=赵乙，两个人都在卡司里，
旧版判据全部放行——账本于是记下了一条不可反驳的错事实。

另外两条：

**同章先后不可判。** 段落位置是叙述顺序，不是事件顺序：回忆、否定、假设、他人对白
都可以把「更早的事」写在后面。所以同一章里同一槽位出现两个都站得住的值时，一律
`conflict` 隔离，不拿靠后的那句覆盖——被覆盖的那个可能是真的，而账本上的错不可逆。

**旧章不倒灌。** 重放或修订只能往更新的章推进当前态；比当前态更旧的章只留流水。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# 硬状态：能投影成当前态、能被用来比对。
HARD_SLOTS: tuple[str, ...] = ("holder", "lifecycle")
# 软状态：只做写作提示与检索排序，永不阻断。时间与地点是题材自己的自由
#（非线性叙事、不可靠叙述者、梦境与回忆本来就该打乱它们）。
SOFT_SLOTS: tuple[str, ...] = ("location", "present", "clock", "count")
ALL_SLOTS: tuple[str, ...] = HARD_SLOTS + SOFT_SLOTS

# 生命周期值。**不含「交出去」**：交出去是换手，不是消耗——
# 「他把钥匙交给乙」之后钥匙完好，把它记成 consumed 会让后面合法使用被判成重复消费。
LIFECYCLE_VALUES: tuple[str, ...] = ("intact", "consumed", "destroyed")

# 只有 holder 可能有机械依据：它的值是人名，人名能在引文里逐字找到。
# lifecycle 的值是从句子里读出来的（「烧了」→destroyed），没有可逐字比对的锚点，
# 所以它只能是 candidate。这不是偷懒，是不肯把模型的理解冒充成引文。
MECHANICAL_SLOTS: tuple[str, ...] = ("holder",)

# 原句下限 6 字。R2 的引文下限是 8，这里放宽两字：状态句常常更短
#（「铜钥在他手里」），但再短就无法证明「这句话说的是这件事」。
MIN_QUOTE_CHARS = 6
MAX_ENTITY_CHARS = 20

STATUS_VERIFIED = "verified"
STATUS_CANDIDATE = "candidate"
STATUS_CONFLICT = "conflict"
STATUS_SUPERSEDED = "superseded"

_TRAILING_PAREN = re.compile(r"[（(][^（()）]*[)）]\s*$")
_WS = re.compile(r"\s+")
_OUTER_QUOTES = "「」『』“”\"'‘’"
_NAME_NOISE = re.compile(r"[，。、；：！？,.;:!?]+")
# 分句：否定与假设只在**本句**内生效，跨句去搜会把上一句的否定算到这一句头上。
_CLAUSE_BREAK = re.compile(r"[，。；！？!?；;、\n]+")
_DIALOGUE_SPAN = re.compile(r"[「『“\"][^」』”\"]{1,300}[」』”\"]")

# 五类「引文里有这个名字、但这句话不是在说『东西在他手上』」：
# 否定、假设、对白、多人同句（动作方向不明）、他被打中（是动作对象不是持有人）。
NEGATION_MARKERS: tuple[str, ...] = (
    "不在", "没有", "并没有", "并未", "并非", "不是", "尚未", "从未",
    "没给", "不给", "别让", "不想", "没能",
)
# 形似否定其实是副词的常用词：不排除它们，「不停地喊他」会被当成否定。
NEGATION_FALSE_FRIENDS: tuple[str, ...] = (
    "不停", "不住", "不由", "不禁", "不觉", "不仅", "不过", "不少", "不久", "不料",
)
HYPOTHESIS_MARKERS: tuple[str, ...] = (
    "如果", "要是", "假如", "倘若", "若是", "万一", "假装", "仿佛", "好像",
    "似乎", "差点", "眼看",
)
# 转移类动词：这些东西后面紧跟的人名是**接收方**。「把铜钥交回陈甲」「塞回沈墨怀里」
# 「搁在沈墨手里」——落点在动词之后。刻意不收「进/入」：那是刺进去、塞进口袋，
# 后面跟的多半是身体部位或容器，不是人。
RECEIVE_MARKERS: tuple[str, ...] = (
    "给", "回", "到", "递", "塞", "推", "交", "放", "搁", "扔", "抛", "送", "按",
)
# 被打中的那个名字不是持有人：「铜刺扎入赵无极左臂」说明赵无极被刺，不是他拿着。
TARGET_VERBS: tuple[str, ...] = (
    "扎入", "扎进", "刺入", "刺进", "捅进", "砍中", "劈中", "击中", "打中",
    "砸向", "扔向", "劈向", "推向", "打在", "砍在", "砸在", "落在",
)


def normalize_name(name: Any) -> str:
    """名字归一：去空白、去末尾括号注释、去句读。**不做模糊匹配。**

    与 ``directing_cast`` 同一条纪律：猜错等于把一个东西记到另一个东西头上，
    而账本上的错是不可逆的。
    """
    text = str(name or "").strip()
    if not text:
        return ""
    text = _TRAILING_PAREN.sub("", text)
    text = _NAME_NOISE.sub("", text)
    text = _WS.sub("", text)
    return text.strip()


def normalize_text(text: Any) -> str:
    """比对用的版式归一：只去空白。全角半角标点不动——那是内容差异。"""
    return _WS.sub("", str(text or ""))


def strip_outer_quotes(text: Any) -> str:
    out = str(text or "").strip()
    while len(out) >= 2 and out[0] in _OUTER_QUOTES and out[-1] in _OUTER_QUOTES:
        out = out[1:-1].strip()
    return out


def quote_grounded(quote: Any, content: str) -> bool:
    """原句是否**逐字**出现在正文里（仅忽略空白/换行/外层引号）。

    刻意不做 R2 那种「最长公共串覆盖度」的放宽：那条判据是为「模型抄来了一段
    近似原文」服务的，而账本要的是「这句话确实写在这里」。放宽一次，账本就会
    多出一行凭空的事实，而它以后再也不会被质疑。
    """
    body = normalize_text(strip_outer_quotes(quote))
    if len(body) < MIN_QUOTE_CHARS:
        return False
    return body in normalize_text(content)


def declared_objects(plan: Any) -> list[str]:
    """① 章纲声明这一章经手的物件。只取名字，不取状态。

    声明本身不是状态：章纲说「这一章会有铜钥」，并不等于铜钥在这一章换了手。
    声明的用途是让这个名字先进册，后面结算是给它的槽位填值。
    """
    if not isinstance(plan, dict):
        return []
    raw = plan.get("objects") or plan.get("props") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("name") or item.get("object") or ""
        name = normalize_name(item)
        if not name or len(name) > MAX_ENTITY_CHARS or name in out:
            continue
        out.append(name)
    return out


def entity_from_quote(name: Any, quote: Any) -> str:
    """② 登记兜底：名字必须**出现在它自己的原句里**才算登记成功。

    这是把「新物件从哪进来」变成确定性问题的唯一办法。模型可以指着正文里没有的
    东西说「这是关键道具」，但它的原句里不会有那个词。
    """
    entity = normalize_name(name)
    if not entity or len(entity) > MAX_ENTITY_CHARS:
        return ""
    if entity not in normalize_text(strip_outer_quotes(quote)):
        return ""
    return entity


def facts_entities(facts: Any, *, persons: set[str]) -> list[tuple[str, str]]:
    """从已核过的事实里取可登记的物件：``(名字, 原句)``。

    ``persons`` 是卡司归一名集合；人物不进本册（人物归角色表管，两处都能改
    等于两处都能改错）。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        quote = str(fact.get("quote") or "")
        for raw in fact.get("entities") or []:
            entity = entity_from_quote(raw, quote)
            if not entity or entity in persons or entity in seen:
                continue
            seen.add(entity)
            out.append((entity, quote))
    return out


def resolve_holder(value: Any, *, persons: dict[str, str]) -> str:
    """持有人归一到卡司键；不在卡司里返回空。

    人物进场有自己的一道门（每章限两个、要落库），账本不能绕过它偷偷造人。
    """
    return persons.get(normalize_name(value), "")


def value_hash(value: Any) -> str:
    """同章同槽位多值时，唯一键要能分开它们。"""
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 持有人方向：名字在引文里，不等于「东西在这个人手上」
# ---------------------------------------------------------------------------


def cast_mentions(quote: Any, *, persons: dict[str, str]) -> list[tuple[int, str]]:
    """引文里出现的卡司人名及位置，按出现顺序。长名优先占位，避免「张三」吃掉「张三丰」。"""
    body = normalize_text(strip_outer_quotes(quote))
    taken = [False] * len(body)
    found: list[tuple[int, str]] = []
    for key in sorted(persons, key=len, reverse=True):
        if not key:
            continue
        start = 0
        while True:
            index = body.find(key, start)
            if index < 0:
                break
            if not any(taken[index : index + len(key)]):
                for pos in range(index, index + len(key)):
                    taken[pos] = True
                found.append((index, persons[key]))
            start = index + len(key)
    found.sort()
    return found


def _clause_bounds(body: str, index: int) -> tuple[int, int]:
    """index 所在分句的 [起, 止)。"""
    left = 0
    for match in _CLAUSE_BREAK.finditer(body, 0, index):
        left = match.end()
    match = _CLAUSE_BREAK.search(body, index)
    return left, (match.start() if match else len(body))


def _clause_has(body: str, index: int, markers: tuple[str, ...]) -> bool:
    """分句里、**这个名字之前**有没有出现这些词。

    只认名字之前：语序上「不在赵乙手里」是在否定它，而「赵乙不在场」说的是别人。
    """
    left, _ = _clause_bounds(body, index)
    prefix = body[left:index]
    # 形似否定其实是副词的先掏掉，否则「不停地喊他」会被当成否定。
    for word in NEGATION_FALSE_FRIENDS:
        prefix = prefix.replace(word, "　" * len(word))
    return any(word in prefix for word in markers)


def _inside_dialogue(body: str, index: int) -> bool:
    return any(m.start() <= index < m.end() for m in _DIALOGUE_SPAN.finditer(body))


def _sentence_bounds(body: str, index: int) -> tuple[int, int]:
    """index 所在句子的 [起, 止)。

    方向只在**同一句**里判：跨句去数「谁最后出现」，等于把下一句里提到的人算成
    这一句的落点。真机回放里 5 条误降级全是这么来的——「取出一枚丹丸，蜡封完好，
    正是沈墨被暗桩截走的那四枚」，落点是赵无极，沈墨只是下一句的修饰语。
    """
    left = 0
    for match in re.finditer(r"[。！？!?；;\n]", body[:index]):
        left = match.end()
    match = re.search(r"[。！？!?；;\n]", body[index:])
    return left, (index + match.start() if match else len(body))


def holder_direction(
    quote: Any, holder: str, *, persons: dict[str, str], entity: str = ""
) -> str:
    """这个持有人是不是「这句话说的那个人」。能确定返回空串，不确定返回原因。

    为什么需要这一层：``verify_change`` 原来的机械锚点是「人名逐字出现在引文里」，
    而真机数据里出现过的反例是「铜刺扎入赵无极左臂」——赵无极确实在引文里，
    但他不是拿着铜刺的人。这类行一旦投影，后面所有涉及铜刺的比对都跟着错。

    判据只覆盖能确定性判的五类，判不了的一律降级为候选（留证、不投影）：
    否定 / 假设 / 对白 / 转移动词之后的不是他 / 是动作的承受者。
    """
    body = normalize_text(strip_outer_quotes(quote))
    mentions = cast_mentions(quote, persons=persons)
    hits = [index for index, name in mentions if name == holder]
    if not hits:
        return "holder-not-in-quote"
    if all(_clause_has(body, index, NEGATION_MARKERS) for index in hits):
        return "negated"
    if all(_clause_has(body, index, HYPOTHESIS_MARKERS) for index in hits):
        return "hypothetical"
    if all(_inside_dialogue(body, index) for index in hits):
        return "in-dialogue"
    # 多人同句：**转移动词之后的那个人**才是落点。
    # 判「最后出现的人名」不行——真机回放里 4 条里 3 条是误降级
    #（「取出一枚丹丸……正是沈墨被暗桩截走的那四枚」，落点其实是赵无极）。
    # 改成看动词：「交回/塞给/搁在/推回 …… 陈甲」里的陈甲才是接收方。
    entity_index = body.find(normalize_name(entity)) if entity else -1
    if entity_index >= 0:
        left, right = _sentence_bounds(body, entity_index)
        receivers = [
            name
            for index, name in mentions
            if left <= index < right
            and index > entity_index
            and not _inside_dialogue(body, index)
            and any(marker in body[max(0, index - 3) : index] for marker in RECEIVE_MARKERS)
        ]
        if receivers and receivers[0] != holder:
            return "holder-is-the-giver-not-the-receiver"
    last = hits[-1]
    tail = body[max(0, last - 6) : last]
    if any(verb in tail for verb in TARGET_VERBS):
        return "holder-is-target-of-action"
    return ""


@dataclass
class Settled:
    """一章抽取结果收敛后的样子。

    ``rejected`` 是「连引文都对不上」的条数；``conflicts`` 是「两个值都站得住、
    但先后不可判」的条数。两个数分开记：前者说明模型在编，后者说明这章有合法的
    多次变化或多声部叙述——前者要改抽取，后者只能隔离。
    """

    changes: list[dict[str, Any]] = field(default_factory=list)
    rejected: int = 0
    conflicts: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    # 降级原因分开记：「名字在引文里但方向判不了」是个可以逐条复核的类别，
    # 混进「候选」里看不见，就会永远没人去改抽取。
    downgrades: dict[str, int] = field(default_factory=dict)

    @property
    def verified(self) -> list[dict[str, Any]]:
        return [c for c in self.changes if c["status"] == STATUS_VERIFIED]


def reject_reason(raw: Any, *, content: str, persons: dict[str, str]) -> str:
    """这条能不能收；能收返回空串，不能收返回原因。"""
    if not isinstance(raw, dict):
        return "not-a-row"
    entity = normalize_name(raw.get("entity") or raw.get("thing") or raw.get("object"))
    slot = str(raw.get("slot") or "").strip().lower()
    value = str(raw.get("value") or "").strip()
    quote = str(raw.get("quote") or "")
    if not entity or len(entity) > MAX_ENTITY_CHARS:
        return "entity-invalid"
    if slot not in ALL_SLOTS:
        return "slot-unknown"
    if not value:
        return "value-empty"
    if not quote_grounded(quote, content):
        return "quote-not-in-prose"
    if entity not in normalize_text(strip_outer_quotes(quote)):
        return "entity-not-in-quote"
    # 人物归角色表管：账本里出现「陈默生命周期=destroyed」这种行，
    # 说明抽取把人物当成了物件。这种行以后会被当成「这个实体被销毁」去比对。
    if entity in persons:
        return "entity-is-a-person"
    if slot == "holder" and not resolve_holder(value, persons=persons):
        return "holder-not-in-cast"
    if slot == "lifecycle" and value not in LIFECYCLE_VALUES:
        return "lifecycle-value-unknown"
    return ""


def verify_change(
    raw: Any, *, content: str, persons: dict[str, str], seq: int = 0
) -> tuple[dict[str, Any] | None, str]:
    """把一条抽取结果变成可入账的行；不能入账返回 ``(None, 原因)``。

    ``status`` 由**值是怎么来的**决定，不由模型自称的置信度决定：
    - holder 且人名逐字在引文里 → `verified`
    - 其余（lifecycle / 软槽位）→ `candidate`
    """
    reason = reject_reason(raw, content=content, persons=persons)
    if reason:
        return None, reason
    entity = normalize_name(raw.get("entity") or raw.get("thing") or raw.get("object"))
    slot = str(raw.get("slot") or "").strip().lower()
    value = str(raw.get("value") or "").strip()
    quote = strip_outer_quotes(raw.get("quote"))
    evidence = ""
    downgrade = ""
    status = STATUS_CANDIDATE
    if slot in MECHANICAL_SLOTS:
        canonical = resolve_holder(value, persons=persons)
        # 空串对任何字符串都「是子串」，不单独拦一下，卡司外的值会一路走到下面。
        if not canonical or canonical not in normalize_text(quote):
            # 引文里没有这个持有人：这句话支持不了这个值。
            return None, "value-not-in-quote"
        value = canonical
        evidence = canonical
        # 名字在引文里只是必要条件。方向判不了就只当候选：留证、不投影。
        # 降级不是拒收——拒收会让这一章「看起来没有状态变化」，而它其实有。
        downgrade = holder_direction(quote, canonical, persons=persons, entity=entity)
        status = STATUS_CANDIDATE if downgrade else STATUS_VERIFIED
    return (
        {
            "entity": entity,
            "slot": slot,
            "value": value,
            "quote": quote,
            "status": status,
            "evidence": evidence,
            "downgrade": downgrade,
            "seq": int(seq),
            "value_hash": value_hash(value),
        },
        "",
    )


def settle_changes(raws: Any, *, content: str, persons: dict[str, str]) -> Settled:
    """把一章的抽取结果收敛：能投影的、只留证的、必须隔离的分开。

    同一 (实体, 槽位) 出现两个**不同**的值 → 全部降为 `conflict`。
    理由见模块开头：段落位置不是事件顺序，靠后的那句可能是回忆或他人对白。
    """
    rows = list(raws or [])
    kept: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    downgrades: dict[str, int] = {}
    for index, raw in enumerate(rows):
        change, reason = verify_change(
            raw, content=content, persons=persons, seq=index
        )
        if change is None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        if change.get("downgrade"):
            key = str(change["downgrade"])
            downgrades[key] = downgrades.get(key, 0) + 1
        kept.append(change)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for change in kept:
        grouped.setdefault((change["entity"], change["slot"]), []).append(change)

    conflicts = 0
    final: list[dict[str, Any]] = []
    for rows_for_slot in grouped.values():
        # 同一个值说了两遍只留一条：唯一键含 value_hash，两条同值行会撞唯一约束，
        # 让整章的账本事务失败。每值取一条代表，优先取 verified 的那条。
        by_value: dict[str, list[dict[str, Any]]] = {}
        for item in rows_for_slot:
            by_value.setdefault(str(item["value"]), []).append(item)
        reps = [
            next((row for row in items if row["status"] == STATUS_VERIFIED), items[0])
            for items in by_value.values()
        ]
        strong = [row for row in reps if row["status"] == STATUS_VERIFIED]
        # 只有**同等强度**的两个值才叫冲突。verified 与 candidate 争先后没有意义：
        # 前者有机械锚点，后者只是读出来的，拿后者去隔离前者，等于让一条没把握的行
        # 否决一条有把握的行。冲突仍只在「两个都站得住」时成立。
        if len(strong) > 1 or (not strong and len(reps) > 1):
            conflicts += len(reps)
            for row in reps:
                final.append({**row, "status": STATUS_CONFLICT})
            continue
        if strong:
            final.append(strong[0])
            # 弱证据仍然留档：它是这一章说过的话，只是不能投影。
            final.extend(row for row in reps if row is not strong[0])
            continue
        final.append(reps[0])
    final.sort(key=lambda item: item["seq"])
    return Settled(
        changes=final,
        rejected=len(rows) - len(kept),
        conflicts=conflicts,
        reasons=reasons,
        downgrades=downgrades,
    )


__all__ = [
    "ALL_SLOTS",
    "HARD_SLOTS",
    "LIFECYCLE_VALUES",
    "MAX_ENTITY_CHARS",
    "MECHANICAL_SLOTS",
    "MIN_QUOTE_CHARS",
    "SOFT_SLOTS",
    "STATUS_CANDIDATE",
    "STATUS_CONFLICT",
    "STATUS_SUPERSEDED",
    "STATUS_VERIFIED",
    "Settled",
    "cast_mentions",
    "declared_objects",
    "entity_from_quote",
    "facts_entities",
    "holder_direction",
    "normalize_name",
    "normalize_text",
    "quote_grounded",
    "reject_reason",
    "resolve_holder",
    "settle_changes",
    "strip_outer_quotes",
    "value_hash",
    "verify_change",
]
