"""终稿版式的**确定性**检查：引号配对、明确截断、标点粘连。

为什么单开一个模块，不放进 `final_audit` 的规则里：

- 「叙事是否自洽」超出模型当前的可靠性（105 章三次换问法，精确率始终 20%–45%），
  但「引号有没有配对」不需要模型参与——它有唯一正确答案。让模型判，等于把一个
  精确率 100%、成本 0 的判定换成概率判定。
- 反过来，凡是需要问「作者本来想写什么」的（句子接不上、事件重演、状态错），
  一律不进这里，继续走 `final_audit` 的 record 通道。**两类东西不能混在一个判据里**：
  2026-10-24 的教训是拿「两句是否不可能同时为真」去复核版式断裂，那对版式是范畴
  错误，实测把这两类规则整条关掉了（召回 1/8）。

跨段对白是唯一必须专门照顾的合法形态：

    他说：“你来了。
    我点点头。
    “我知道。”他说。

段末不闭、下段另起开引号，是通行排版。因此判定方法定为**只看章末残留的引号深度**，
不在段中报警：段中不闭有多少种解释，章末未闭只有一种——漏了闭引号。这样「漏一个
闭引号」与「合法跨段对白」才不混。

阻断口径见 `BLOCKING_RULES`：只收「误拦可恢复」的两类。可恢复指改稿能解决，
而不是靠人手工放行——一个必须人工放行才能过的门，等于没有门。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# “ ” 与 ‘ ’。英文直引号 " ' 不参与：中文正文里它更多是英文短语/撇号，
# 按配对统计会把 `don't` 里的撇号算成开引号。
QUOTE_PAIRS: dict[str, str] = {"\u201c": "\u201d", "\u2018": "\u2019"}

RULE_UNCLOSED_QUOTE = "unclosed_quote"
RULE_TRUNCATED_TAIL = "truncated_tail"
RULE_DANGLING_PUNCT = "dangling_punct"
RULE_RESIDUE_MARKUP = "residue_markup"

RULE_LABELS: dict[str, str] = {
    RULE_UNCLOSED_QUOTE: "引号未闭合",
    RULE_TRUNCATED_TAIL: "章末截断",
    RULE_DANGLING_PUNCT: "标点粘连",
    RULE_RESIDUE_MARKUP: "排版残留",
}

# 只有这两类进阻断。`dangling_punct` 不进：它有唯一解（删掉多出来的那个），
# 交给 `autofix` 归一即可，不需要让人或模型决定。
BLOCKING_RULES: frozenset[str] = frozenset({RULE_UNCLOSED_QUOTE, RULE_TRUNCATED_TAIL})

# 章末合法的终止字符。省略号（… 与 ...）算终止：以省略号收尾是常见且合法的章末。
TERMINAL_CHARS: frozenset[str] = frozenset(
    "。！？…》”’」』）】”』!.?…"
)

# 章末若停在下列字符上，是「话没说完就断了」的确定形态。
DANGLING_TAIL_CHARS: frozenset[str] = frozenset("，、：；—“‘（【《,;:")

# 章末若是这些 ASCII 记号，是**排版残留**（markdown 反引号、星号、井号、竖线），
# 不是「话没说完」。把残留报成截断会让人去重写一段本来完整的正文——校准时
# 唯一命中的那条就是这种形态（章末一个反引号）。残留归 `residue_markup`，
# 同样不阻断：它只需要有人清掉一个符号。
RESIDUE_MARKERS: frozenset[str] = frozenset("`*#~^|<>/\\")

# 连续同类标点里，属于作者强调而非错误的组合。`……` 与 `——` 是标准符号，
# `！！`/`？？`/`！？` 在网文里是常态；其余两连一律按笔误处理。
LEGIT_DOUBLES: frozenset[str] = frozenset({"……", "——", "！！", "？？", "！？", "？！"})

# 中文正文里混进半角标点的确定形态：汉字紧邻半角 , ; : 。不含 `.`，
# 因为 `3.5`、`v1.2` 这类数字与版本号是合法的。
HALFWIDTH_BAD = {",": "，", ";": "；", ":": "："}


@dataclass(frozen=True)
class LayoutDefect:
    """一处版式缺陷。`offset` 是正文里的字符下标，供改稿定位用。"""

    rule_id: str
    message: str
    excerpt: str
    offset: int = -1

    @property
    def blocking(self) -> bool:
        return self.rule_id in BLOCKING_RULES

    def as_payload(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "label": RULE_LABELS.get(self.rule_id, self.rule_id),
            "message": self.message,
            "excerpt": self.excerpt,
            "offset": self.offset,
            "blocking": self.blocking,
        }


def _snippet(text: str, offset: int, width: int = 24) -> str:
    """取缺陷附近的原文，用于人工核对——没有原文的报警等于要求人相信程序。"""
    start = max(0, offset - width // 2)
    return text[start: start + width].replace("\n", "⏎")


def unclosed_quotes(text: str) -> list[LayoutDefect]:
    """章末仍未闭合的引号。段中不闭不报，见模块说明。

    判定只看一个量：按「段末未闭 + 下段以开引号起 = 隐式闭合」这条排版惯例
    解释完之后，章末还剩几层没闭。剩 0 层，无论中间跨了多少段，都是合法的。
    """
    depth: list[tuple[int, str]] = []  # (offset, 开引号字符)
    lines = str(text or "").split("\n")
    offset = 0
    for index, line in enumerate(lines):
        for char in line:
            if char in QUOTE_PAIRS:
                depth.append((offset, char))
            elif depth and char == QUOTE_PAIRS[depth[-1][1]]:
                depth.pop()
        offset += len(line) + 1
        if depth and index + 1 < len(lines):
            nxt = lines[index + 1].lstrip()
            if nxt and nxt[0] in QUOTE_PAIRS:
                # 跨段同一段话：承认隐式闭合，下段自己会重新开引号。
                depth.clear()
    if not depth:
        return []
    return [
        LayoutDefect(
            rule_id=RULE_UNCLOSED_QUOTE,
            message=f"章末仍有 {len(depth)} 个引号未闭合（开头「{char}」）",
            excerpt=_snippet(text, spot),
            offset=spot,
        )
        for spot, char in depth
    ]


def truncated_tail(text: str) -> list[LayoutDefect]:
    """章末截断：最后一句话停在「没说完」的字符上，或压根没有终止标点。

    只报最后一句。中间少标点属于文风，不在这里判——那一类要靠读全文才知道
    作者是不是故意的，而那时判据已经不是版式了。
    """
    body = str(text or "").rstrip()
    if not body:
        return []
    tail = body[-1]
    if tail in TERMINAL_CHARS:
        return []
    if tail in RESIDUE_MARKERS:
        # 不是截断，交给 residue_markup。这里必须明确让路，不能落到下面的
        # 「没有终止标点」分支——否则残留符号会被当成「话没说完」。
        return []
    kind = "停在未完成标点上" if tail in DANGLING_TAIL_CHARS else "没有句末终止标点"
    return [
        LayoutDefect(
            rule_id=RULE_TRUNCATED_TAIL,
            message=f"章末{kind}（末尾字符「{tail}」）",
            excerpt=_snippet(body, len(body) - 1, width=48),
            offset=len(body) - 1,
        )
    ]


def dangling_punct(text: str) -> list[LayoutDefect]:
    """标点粘连：同类标点误重复，或汉字直接贴半角标点。

    这类不需要任何人参与判断，`autofix` 能唯一地改好，所以它不阻断。
    """
    body = str(text or "")
    out: list[LayoutDefect] = []
    index = 0
    while index + 1 < len(body):
        pair = body[index: index + 2]
        if pair[0] == pair[1] and pair[0] in "，。、；：？！" and pair not in LEGIT_DOUBLES:
            out.append(
                LayoutDefect(
                    rule_id=RULE_DANGLING_PUNCT,
                    message=f"标点重复「{pair}」",
                    excerpt=_snippet(body, index),
                    offset=index,
                )
            )
            index += 2
            continue
        char = body[index]
        if char in HALFWIDTH_BAD and index > 0:
            prev_ch = body[index - 1]
            next_ch = body[index + 1] if index + 1 < len(body) else ""
            if _is_han(prev_ch) and _is_han(next_ch):
                out.append(
                    LayoutDefect(
                        rule_id=RULE_DANGLING_PUNCT,
                        message=f"汉字之间用了半角「{char}」，应为「{HALFWIDTH_BAD[char]}」",
                        excerpt=_snippet(body, index),
                        offset=index,
                    )
                )
        index += 1
    return out


def _is_han(char: str) -> bool:
    return bool(char) and "\u4e00" <= char <= "\u9fff"


def residue_markup(text: str) -> list[LayoutDefect]:
    """排版残留：中文正文里混进了 markdown 记号。

    只报确定是残留的三种，且每章每类只报第一处——校准的目的是让人去清掉，
    不是把同一处刷成十条。`***` / `---` 独占一行不算：那是网文常见的分段符。
    """
    body = str(text or "")
    out: list[LayoutDefect] = []

    spot = body.find("`")
    if spot >= 0:
        out.append(
            LayoutDefect(
                rule_id=RULE_RESIDUE_MARKUP,
                message="正文里有反引号（markdown 代码记号）",
                excerpt=_snippet(body, spot),
                offset=spot,
            )
        )

    heading = re.search(r"(?m)^#{1,6}\s", body)
    if heading:
        out.append(
            LayoutDefect(
                rule_id=RULE_RESIDUE_MARKUP,
                message="行首有 markdown 标题记号",
                excerpt=_snippet(body, heading.start()),
                offset=heading.start(),
            )
        )

    bold = re.search(r"\*\*[^*\n]{1,40}\*\*", body)
    if bold:
        out.append(
            LayoutDefect(
                rule_id=RULE_RESIDUE_MARKUP,
                message="正文里有成对星号（markdown 加粗记号）",
                excerpt=_snippet(body, bold.start()),
                offset=bold.start(),
            )
        )
    return out


def check_layout(text: str) -> list[LayoutDefect]:
    """全部版式检查，按正文位置排序。纯函数，无模型、无 I/O、无随机。"""
    defects = (
        unclosed_quotes(text)
        + truncated_tail(text)
        + dangling_punct(text)
        + residue_markup(text)
    )
    return sorted(defects, key=lambda d: (d.offset, d.rule_id))


def blocking_defects(defects: list[LayoutDefect]) -> list[LayoutDefect]:
    return [item for item in defects if item.blocking]


def autofix(text: str) -> tuple[str, list[str]]:
    """只做**有唯一解**的修补：标点重复、汉字间半角标点。

    故意不补未闭合的引号：缺的那一个可能该落在章中间的任何一处，补在末尾
    只是让检查闭嘴，不是把稿子改对。那种情况得重写，不能静默归一。
    返回 (新正文, 修了什么)。
    """
    body = str(text or "")
    repaired: list[str] = []
    out: list[str] = []
    index = 0
    while index < len(body):
        if index + 1 < len(body):
            pair = body[index: index + 2]
            if (
                pair[0] == pair[1]
                and pair[0] in "，。、；：？！"
                and pair not in LEGIT_DOUBLES
            ):
                out.append(pair[0])
                repaired.append(f"合并重复标点「{pair}」")
                index += 2
                continue
        char = body[index]
        if (
            char in HALFWIDTH_BAD
            and 0 < index < len(body) - 1
            and _is_han(body[index - 1])
            and _is_han(body[index + 1])
        ):
            out.append(HALFWIDTH_BAD[char])
            repaired.append(f"半角「{char}」→「{HALFWIDTH_BAD[char]}」")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out), repaired


def gate_mode(production: dict[str, Any] | None) -> str:
    """阻断口径：``off`` 不检查，``record`` 只留档（缺省），``block`` 真拦。

    缺省 record 的理由和终审一致：先让它在真实语料上跑出误报率，再决定要不要
    让它拦人。确定性的判据也不能免掉这一步——判据确定性 ≠ 判据覆盖的形态
    就一定是缺陷（「。。」可能是作者的分段习惯）。
    """
    raw = str(((production or {}).get("layout_gate") or "record")).strip().lower()
    return raw if raw in {"off", "record", "block"} else "record"


__all__ = [
    "BLOCKING_RULES",
    "LayoutDefect",
    "RULE_DANGLING_PUNCT",
    "RULE_LABELS",
    "RULE_RESIDUE_MARKUP",
    "RULE_TRUNCATED_TAIL",
    "RULE_UNCLOSED_QUOTE",
    "autofix",
    "blocking_defects",
    "check_layout",
    "dangling_punct",
    "gate_mode",
    "residue_markup",
    "truncated_tail",
    "unclosed_quotes",
]
