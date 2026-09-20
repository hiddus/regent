"""Director evidence grounding rules."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from regent.novel.domain.errors import ProductionStopped

QUOTE_MIN_RUN = 10
QUOTE_MIN_RATIO = 0.5
MAX_JUDGE_REPAIRS = 2
MAX_COMMAND_REPAIRS = 2
COMMAND_REPAIR_RULE = (
    "??????????????????????"
    "?????????????????????????? revised_brief??????"
    "???????????????????????????????????"
)


def _longest_common_run(quote: str, text: str) -> int:
    """两段文字的最长**连续**公共子串长度（只数逐字相同，不做模糊匹配）。"""
    if not quote or not text:
        return 0
    # 滚动数组 DP；两端都是千字量级，不必上后缀自动机。
    prev = [0] * (len(text) + 1)
    best = 0
    for char in quote:
        cur = [0] * (len(text) + 1)
        for j, other in enumerate(text, start=1):
            if char == other:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


EVIDENCE_REPAIR_RULE = (
    "evidence 须给出可定位的场景依据：从下面原文摘取关键短语或短句即可，"
    "不必整句逐字抄录；禁止凭空捏造未出场的内容。\n"
    "指出正文缺失时可用「全文无… / 未见…」（所缺内容须确实未出现）；"
    "肯定性依据仍须摘自原文。\n"
    "不要加字段名；不要把互不相关的两段拼成一条。"
)


async def _grounded_judgment[T: BaseModel](
    call: Any,
    schema: type[T],
    system: str,
    payload: dict[str, Any],
    stage_text: str,
    label: str,
    *,
    coerce: Any = None,
    command_check: Any = None,
    command_report: Any = None,
) -> T:
    """带反馈自修的导演判断：不合规时把**原因**回传，而不是判死整章。

    两类问题共用一条自修通道，因为 ``CommandRejected`` 是 ``ProductionStopped``
    的子类，而两者的共同点是：**模型看不见自己错在哪**。

    - 引文不合规 → 回传「哪条引用、差多少、原文就在下面」；
    - 命令被 Runtime 拒绝 → 回传拒绝原因，请它改选一个合法动作。

    自修必须换 command_id：沿用原 id 会被幂等键挡住，或复用上一次的坏结果。
    自修次数用尽才判死——判据一次都没放松，只是给模型看见错误的机会。

    **反馈是累积的**：第二次失败不能把第一次的约束顶掉。真机就是这样死的——引文
    改好了、命令却被拒，按命令改完动作又把引文退回坏样子，最后一次两边都没过。

    ``coerce`` 在校验**之前**就地把注定非法的动作换掉（留痕由调用方收集），
    ``command_check`` 随后校验替换后的动作，两者顺序不能颠倒。
    """
    repair: list[str] = []
    # 耗尽时必须带上**最后一次被拒的原因**：只报"次数用尽"会把 Runtime 的真实
    # 理由吞掉，既没法诊断，也会让依赖该理由的判据静默失效。
    last_reason: str | None = None
    attempts = max(1, MAX_JUDGE_REPAIRS + MAX_COMMAND_REPAIRS)
    for repair_no in range(attempts):
        result = await call(
            schema,
            system,
            {**payload, "repair_instructions": repair} if repair else payload,
            repair_no=repair_no,
        )
        try:
            _quote_check(result.evidence, stage_text)
        except ProductionStopped as exc:
            last_reason = str(exc)
            # 累积：之前已经告诉过它的约束不能丢。
            repair = [*repair, f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE]
            if not any(item == stage_text[:1500] for item in repair):
                repair.append(stage_text[:1500])
            continue
        if coerce is not None:
            coerce(result)
        if command_check is not None:
            try:
                command_check(result)
            except ProductionStopped as exc:
                # 不是引文问题：得让导演**改选一个动作**，光给原文没用。
                last_reason = str(exc)
                repair = [*repair, f"上一版动作不能执行：{exc}"]
                if command_report is not None:
                    repair.append(
                        "当前状态下各动作的可执行性：\n" + "\n".join(command_report(result))
                    )
                repair.append(COMMAND_REPAIR_RULE)
                continue
        return result
    raise ProductionStopped(
        f"{label}：自修次数已用尽"
        + (f"，最后一次未通过的原因：{last_reason}" if last_reason else "")
    )


def _stage_text(take: dict[str, Any]) -> str:
    """场上**可引用**的原文：事件陈述、表演（动作与台词）、规则冲突提示。

    ``rule_issues`` 必须在内：提示词要求「有 rule_issues 必须重演」，而重演的
    **理由**就是那条规则冲突本身。不把它算作可引用文本，等于要求导演拿一个
    不许引用的东西当证据——它只能把规则提示复述进 evidence，再被判成捏造。
    """
    return "\n".join(
        [str(e.get("statement", "")) for e in take.get("events") or []]
        + [
            line
            for a in take.get("performances") or []
            for line in list(a.get("actions") or []) + list(a.get("dialogue") or [])
        ]
        + [str(issue) for issue in take.get("rule_issues") or []]
    )


# 省略号：模型引长句时几乎必然跳读，跳读不等于捏造。
_QUOTE_ELLIPSIS = re.compile(r"…|\.{2,}|．{2,}")
# 「原话」——评注：导演常把可核对方括号引文与评注拼成一条 evidence。
_QUOTE_SPAN = re.compile(r"[「『\"“]([^」』\"”]{4,})[」』\"”]")
_QUOTE_DASH = re.compile(r"—{1,2}|-{2,}")
# 「全文无X」：审阅时指出缺失的负向证据；X 须确实未在正文出现。
_ABSENCE_CLAIM = re.compile(
    r"^(?:全文|正文|稿面|场上|当前(?:正文|草稿)?)?(?:无|没有|未见|未出现|未写到|未写|缺少|缺失)(.+)$"
)
_ABSENCE_EMBEDDED = re.compile(
    r"(?:^|[，,；;。])\s*((?:全文|正文|稿面|场上|当前(?:正文|草稿)?)?(?:无|没有|未见|未出现|未写到|未写|缺少|缺失)[^，。；;\n]{3,})"
)
# 空白：引文把 draft 的段落换行压成句号是版式差异，不是内容差异；覆盖判据
# 不能因为一个换行把真实引文判成伪造。fabrication（凭空的词）依然会被拒。
_WS = re.compile(r"\s+")
_FULLWIDTH_PUNCT = str.maketrans({"　": " ", "，": ",", "。": ".", "：": ":"})
_OUTER_QUOTE_CHARS = "「」『』\"“”'"


def _strip_outer_quotes(s: str) -> str:
    """去掉包裹整段的引号，避免『你猜。』因括号样式与正文「你猜。」不一致而误杀。"""
    t = s.strip()
    while len(t) >= 2 and t[0] in _OUTER_QUOTE_CHARS and t[-1] in _OUTER_QUOTE_CHARS:
        t = t[1:-1].strip()
    return t


def _normalize_ws(s: str) -> str:
    return _WS.sub("", _strip_outer_quotes(s).translate(_FULLWIDTH_PUNCT))


def _covered(quote: str, text: str) -> bool:
    """逐字覆盖度判据（不含省略号拆分）。见 ``_quote_check`` 的说明。

    引文与正文都先做**空白归一**——段落换行、全角空格、全角标点后的空格都是
    版式差异，不是内容差异。
    """
    if not quote:
        return False
    q = _normalize_ws(quote)
    t = _normalize_ws(text)
    if q in t:
        return True
    run = _longest_common_run(q, t)
    need = min(len(q), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(q))))
    return run >= need


def _citation_spans(quote: str) -> list[str]:
    """抽出可单独核对的「原话」片段（引号内、破折号前），不含评注。"""
    spans: list[str] = []
    for match in _QUOTE_SPAN.finditer(quote):
        span = match.group(1).strip()
        if span and span not in spans:
            spans.append(span)
    parts = [p.strip() for p in _QUOTE_DASH.split(quote) if p.strip()]
    if len(parts) > 1:
        head = parts[0]
        for left, right in (("「", "」"), ("『", "』"), ('"', '"'), ("“", "”")):
            if head.startswith(left) and head.endswith(right) and len(head) > 2:
                head = head[len(left) : -len(right)].strip()
                break
        if head and head not in spans:
            spans.append(head)
    return spans


def _absence_claim_parts(quote: str) -> list[str] | None:
    """若是「全文无X」形态，返回待核对「确实未出现」的片段；否则 None。"""
    match = _ABSENCE_CLAIM.match(quote.strip())
    if not match:
        return None
    missing = match.group(1).strip(' ：:，,。；;.!！？?「」『』"“”')
    if not missing:
        return None
    parts = [p.strip(' 「」『』"“”') for p in re.split(r"[、,/]|以及|和", missing) if p.strip()]
    return parts or [missing]


def _mentions(needle: str, text: str) -> bool:
    """正文是否提到 needle（比 ``_covered`` 更怕漏检：短词允许隔字顺序命中）。"""
    if _covered(needle, text):
        return True
    q = _normalize_ws(needle)
    t = _normalize_ws(text)
    if not q:
        return False
    if q in t:
        return True
    run = _longest_common_run(q, t)
    if run >= min(len(q), max(4, int(0.5 * len(q)))):
        return True
    # 「掐手臂」vs「掐了一把……手臂」：短词按汉字顺序子序列命中
    if len(q) <= 8:
        idx = 0
        for char in t:
            if idx < len(q) and char == q[idx]:
                idx += 1
        return idx == len(q)
    return False


def _is_absence_grounded(quote: str, text: str) -> bool:
    """负向证据：声称缺失的内容须确实未在正文出现（有一段命中则声称不成立）。"""
    parts = _absence_claim_parts(quote)
    if not parts:
        return False
    # 整段缺失声称也要核对（避免「掐手臂、很长的胡编」只因后半不存在而误放行）
    missing = _ABSENCE_CLAIM.match(quote.strip())
    assert missing is not None
    whole = missing.group(1).strip(' ：:，,。；;.!！？?「」『』"“”')
    checkable = [p for p in ([whole, *parts]) if len(_normalize_ws(p)) >= 3]
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for part in checkable:
        if part not in seen:
            seen.add(part)
            uniq.append(part)
    if not uniq:
        return False
    return not any(_mentions(part, text) for part in uniq)


def _embedded_absence_claims(quote: str) -> list[str]:
    """抽出整句或夹在评述里的「全文无X」声称。"""
    stripped = quote.strip()
    claims: list[str] = []
    if _absence_claim_parts(stripped) is not None:
        claims.append(stripped)
    for match in _ABSENCE_EMBEDDED.finditer(stripped):
        claim = match.group(1).strip()
        if claim and claim not in claims:
            claims.append(claim)
    return claims


def _is_grounded(quote: str, text: str) -> bool:
    """引文是否确有出处。

    允许用省略号跳读——但**每一段被留下的片段都要有出处**，判据与整条引用同一套
    （覆盖度，不是字节级）。省略号只是把若干真实片段接起来，不是改写许可证。

    也允许「原话」——评注：方括号/引号内（或破折号前）只要有一段足够长且有出处，
    整条 evidence 就算接地。评注本身不必出现在正文里。

    也允许「全文无X」负向证据（可与原话拼在同一条）：X 须确实未在正文出现。
    注意：整句就是「全文无『X』」时，X 只作缺失核对，不能再当成要命中的原话。

    真机样本：5 条「事实证据不在正文中」全部来自 ``A……B`` 这种跳读引用，模型给的是
    ``passed=True``，是被字节级比对改判成硬失败；而硬失败又会连锁导致导演的 ACCEPT
    被 Runtime 拒绝、整章判死。要求每段都**字节级**存在又过严——模型常把其中一段
    轻微改写，那不是捏造。
    """
    stripped = quote.strip()
    if not stripped:
        return False
    if stripped in text:
        return True
    # 整句覆盖度先过：省略号拆段是加严路径，不能反而否掉「整句已够重合」的引文
    if _covered(stripped, text):
        return True
    # 整句负向证据优先，避免「全文无『X』」被拆成既要 X 在场又要 X 不在场
    if _ABSENCE_CLAIM.match(stripped):
        return _is_absence_grounded(stripped, text)
    spans = [s for s in _citation_spans(stripped) if s.strip()]
    span_hit = any(_covered(span, text) for span in spans)
    absences = _embedded_absence_claims(stripped)
    absence_hit = bool(absences) and all(_is_absence_grounded(a, text) for a in absences)
    if spans and absences:
        # 「跳到『原话』，全文无X」：两边都要站得住
        if span_hit and absence_hit:
            return True
    elif span_hit or absence_hit:
        return True
    segments = [seg for seg in _QUOTE_ELLIPSIS.split(stripped) if seg.strip()]
    if len(segments) > 1:
        return all(_covered(seg.strip(), text) for seg in segments)
    return False


def _quote_check(quotes: list[str], text: str) -> None:
    """核对导演的 evidence 确有出处。

    判据是**最长逐字重合**够长，而不是全串字节相同。阈值取「至少 QUOTE_MIN_RUN 字」与
    「引文本身的 QUOTE_MIN_RATIO」中较大者，再以引文长度为上限——短引文天然要求整串命中，
    长引文允许改掉几个字。
    """
    if not quotes:
        raise ProductionStopped("导演判断缺少可核对的原文证据")
    for quote in quotes:
        stripped = quote.strip()
        if not stripped:
            raise ProductionStopped("导演判断缺少可核对的原文证据：存在空白引用")
        if _is_grounded(stripped, text):
            continue
        q = _normalize_ws(stripped)
        t = _normalize_ws(text)
        run = _longest_common_run(q, t)
        need = min(len(q), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(q))))
        if run >= need:
            continue
        raise ProductionStopped(
            f"导演判断缺少可核对的原文证据：引用「{stripped[:40]}」"
            f"与场上原文最长逐字重合 {run} 字，需要 {need} 字；"
            "若用省略号跳读，每一段留下的片段都必须逐字存在"
        )
