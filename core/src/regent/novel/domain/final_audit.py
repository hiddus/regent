"""最终稿复验的判定部分：规则、证据核对、阻断口径。

为什么要把「校稿」与「终审」分开：

- 责编审校（editor_audit）为不误伤正常写作，全部规则被强制 soft；这对「读起来
  顺不顺」合适，对「有没有写错人」不合适。硬错误进正史一次，后面几十章都跟着错。
- 硬门（check_chapter）只看字数和照抄，看不到「把张三写成李四」「已经毁掉的剑
  又拔出来」「同一笔资源扣了两次」。
- 所以终审单独一套规则，只收**客观可核对**的错，而且每条都必须能在正文里指出
  原文：指不出原文的，一律不算证据。模型说「这里有人名不对」但没有原文，等于
  让模型凭印象否掉一整章。

「疑似名字不能直接合并」是另一条硬约束：长得像的名字不自动改成卡司名。改错了
是覆盖，改对了才是修；分不清时只能报给人看，不能猜。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# 只收这七类：都能用「正文里发生了什么」判断，不靠文风偏好。
FINAL_RULES: tuple[str, ...] = (
    "identity_switch",  # 把人写成另一个人：错人名、错身份、错亲属关系
    "possession_error",  # 持有物错位；已毁/已交出/已丢失的东西又被使用
    "consumption_repeat",  # 已消耗的资源再次可用，或同一笔消耗被重复扣
    "event_repeat",  # 已经发生过的同一场又发生一次（不是回忆、转述、引用）
    "place_jump",  # 地点跳变：没有交代移动就到了别处
    "seam_break",  # 删段/改写之后断开：句子不接、因果缺环、对话没有来处
    "timeline_conflict",  # 与已发生事件的时间、先后、已知信息冲突
)

RULE_LABELS: dict[str, str] = {
    "identity_switch": "人物身份",
    "possession_error": "持有物",
    "consumption_repeat": "资源消耗",
    "event_repeat": "事件重演",
    "place_jump": "地点",
    "seam_break": "句段接续",
    "timeline_conflict": "已发生事件",
}

# 一条引文至少这么长才能定位到具体句子；「他说」这种片段核对不了任何事。
MIN_QUOTE_CHARS = 8

# 这四类要跟一条**已成立的事实**对着看：光有引文还不够，还要指出跟哪一句冲突。
# 另外三类（错人、地点跳变、句段断裂）是章内自洽问题，不需要外部事实。
FACT_RULES: frozenset[str] = frozenset(
    {"possession_error", "consumption_repeat", "event_repeat", "timeline_conflict"}
)
MIN_FACT_CHARS = 6


@dataclass(frozen=True)
class AuditIssue:
    rule_id: str
    quote: str
    reason: str
    # 与这条错冲突的那句已成立事实/上一章原句/本章另一处原文，逐字复制。
    # 空着等于「模型觉得不对」，不算证据。
    fact: str = ""

    def as_payload(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "quote": self.quote,
            "reason": self.reason,
            "fact": self.fact,
        }


def squash(text: str) -> str:
    """比对前只归版式：换行、缩进、分节不同不算篡改，改词才算。"""
    return "".join(str(text or "").split())


def ground_issues(
    issues: Iterable[AuditIssue],
    chapter: str,
    *,
    facts: Iterable[str] | None = None,
) -> tuple[list[AuditIssue], list[str]]:
    """只留下引文能在正文里找到、规则在白名单内、冲突事实能对上的错。

    四条判据，全在代码里确定性执行：

    1. 规则在白名单内；
    2. 引文是正文的连续片段（去空白后比对）且够长；
    3. 同一段引文只认一条——一句话既「持有物错」又「时间线错」又「接不上」时，
       三条其实是一个判断，重复计数只会把一次误读放大成三次；
    4. 持有物/消耗/事件/时间线四类还须给出 ``fact``：与它冲突的那句已成立事实、
       上一章原句或本章另一处原文，逐字复制，且能在（事实 + 上一章 + 本章去掉
       被引那句之后的正文）里找到。

    第 4 条是误杀的闸门。真机事故（2026-10-24，C1 第 11 章）：正文写「裴无咎长
    什么样？他记得这个名字。记得残方上的回声草……但夹墙在哪一面墙？」——这是
    故意写成「记得碎片、忘了关键」的叙述，终审却把它读成「记得＝持有」，同一句
    报出持有物、时间线、句段三条硬错，一章被判死。让模型指认「跟哪句话冲突」，
    指不出来就不是证据。

    拒收的每一条都留下理由：事后要能看出是「模型没找到证据」还是「规则写错了」。
    """
    body = squash(chapter)
    haystack = squash("".join(str(x) for x in (facts or ()))) if facts is not None else ""
    kept: list[AuditIssue] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in issues:
        rule = str(item.rule_id or "").strip()
        quote = str(item.quote or "").strip()
        fact = str(item.fact or "").strip()
        if rule not in FINAL_RULES:
            dropped.append(f"规则不在白名单：{rule or '(空)'}")
            continue
        squeezed = squash(quote)
        if len(squeezed) < MIN_QUOTE_CHARS:
            dropped.append(f"{rule}：引文过短，无法定位")
            continue
        if squeezed not in body:
            dropped.append(f"{rule}：引文不在正文里（{quote[:24]}）")
            continue
        if squeezed in seen:
            dropped.append(f"{rule}：同一句已有一条结论，不重复计数（{quote[:24]}）")
            continue
        if facts is not None and rule in FACT_RULES:
            if len(squash(fact)) < MIN_FACT_CHARS:
                dropped.append(f"{rule}：没给出与它冲突的那句事实（{quote[:24]}）")
                continue
            # 本章去掉被引那句之后仍可作证：章内前后矛盾也算，
            # 但「用被指的那句证明它自己错」不算。
            corpus = haystack + body.replace(squeezed, "", 1)
            if squash(fact) not in corpus:
                dropped.append(f"{rule}：找不到与它冲突的原句，按误读丢弃（{quote[:24]}）")
                continue
        seen.add(squeezed)
        kept.append(
            AuditIssue(
                rule_id=rule,
                quote=quote,
                reason=str(item.reason or "").strip(),
                fact=fact,
            )
        )
    return kept, dropped


def format_issue(issue: AuditIssue) -> str:
    """给改稿提示用的一句话：规则 + 原因 + 原文摘录。"""
    snip = (issue.quote or "").replace("\n", " ")[:80]
    label = RULE_LABELS.get(issue.rule_id, issue.rule_id)
    return f"[final:{issue.rule_id}] {label}：{issue.reason}｜摘录「{snip}」"


def audit_status(production: dict[str, Any], content_hash: str) -> str:
    """这一稿的终审状态：``passed`` / ``failed`` / ``unavailable`` / ``stale``。

    ``stale`` 是最危险的一种：审查过的是另一份正文，当前这份没审过。删段、换探针
    稿、重写之后必须按新的 prose_hash 重做——旧审查不能拿来给新稿背书。
    """
    audit = production.get("final_audit")
    if not isinstance(audit, dict) or not audit:
        return "stale"
    if str(audit.get("hash") or "") != str(content_hash or ""):
        return "stale"
    status = str(audit.get("status") or "")
    return status if status in {"passed", "failed", "unavailable"} else "stale"


def layout_blockers(production: dict[str, Any], content_hash: str) -> list[str]:
    """版式缺陷的阻断项。确定性判据，与模型结论分开算。

    只有 `block` 口径才真拦；缺省 `record`。理由与终审一致：先让它在真实稿上
    跑出误报率。240 章真实稿实测：这一类缺陷 0 命中、0 误报，但真正常见的
    「markdown 残留」不在阻断集里（它只需清掉一个符号，不需要重写）。
    """
    from regent.novel.domain import prose_layout

    stored = production.get("layout")
    if not isinstance(stored, dict) or not stored:
        return []
    if str(stored.get("hash") or "") != str(content_hash or ""):
        # 检查过的是另一份正文。不在这里拦：`final_audit_stale` 已经覆盖
        # 「稿变了没重审」，再加一条只会让同一个原因报两次。
        return []
    if prose_layout.gate_mode(production) != "block":
        return []
    count = int(stored.get("blocking") or 0)
    return [f"layout_defects:{count}"] if count else []


def canon_blockers(production: dict[str, Any], content_hash: str) -> list[str]:
    """入正史前的终审阻断项。空列表表示这一稿审过且没有已确认的硬错。

    ``mode`` 是放行口径，不是判据：``record`` 只留档（默认），``block`` 才阻断。
    标定为「record」是因为 2026-10-24 的标注集显示三道复核后的拦截精确率只有
    23%–46%，按它阻断会把三成正常章判死；口径没定之前不允许它自己变成拦路虎。
    """
    blockers = layout_blockers(production, content_hash)
    status = audit_status(production, content_hash)
    if status == "stale":
        return blockers + ["final_audit_stale"]
    if status == "failed":
        if str((production.get("final_audit") or {}).get("mode") or "record") != "block":
            return blockers
        audit = production.get("final_audit") or {}
        return blockers + [f"final_audit_issues:{len(audit.get('issues') or [])}"]
    return blockers
