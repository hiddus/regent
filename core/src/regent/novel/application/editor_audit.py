"""专职责编审校：补现有 VALIDATE / commons / WATCH 的读者向盲区。

现有审校各管一块，都不问「读者听不听得懂」：
- WATCH_PROSE：对照 brief/exit，界面出现即可 ACCEPT
- VALIDATE：正文 ↔ 结算事件一致性
- commons：器物 UX + 本作公约（顾问级）
- 章级 ChapterValidation：连续性/因果/节点完成

本模块以网文责编视角做独立调用，硬问题并入 REVIEW 失败通道。
"""

from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel, Field


class EditorIssue(BaseModel):
    rule_id: str = Field(min_length=1, max_length=64)
    hard: bool = True
    quote: str = Field(min_length=1, description="正文摘录，须能在当前章定位")
    reason: str = Field(min_length=8)


class EditorAuditResult(BaseModel):
    passed: bool
    issues: list[EditorIssue] = Field(default_factory=list)


# 责编检查清单：与 VALIDATE/commons 刻意错开
EDITOR_RULE_IDS: tuple[str, ...] = (
    "jargon_first_gloss",  # 发明术语首次出现无读者向释义
    "power_readable",  # 金手指：能做/不能做/代价读者听不懂
    "intra_chapter_contradiction",  # 同章事实/身份/对话自相矛盾
    "title_cast_mismatch",  # 章题人名与正文主角不一致
    "closed_set_without_scope",  # 局部名单写成全集且无「目前」限定
    "info_debt",  # 角色当已知、读者未被告知
    "redundant_beats",  # 同章有效信息重复堆叠
    "causal_unlink",  # 关键选择与眼前困境无因果挂钩
    "metaphor_overuse",  # 同一隐喻刷屏掩盖信息
    "contract_invisible",  # 本章对读者契约完全零触达（仅提示）
)

# 责编默认全部 soft：明显硬伤由 VALIDATE/front_gate 前置；责编只校稿。
_FORCE_SOFT_RULES: frozenset[str] = frozenset(EDITOR_RULE_IDS)


def should_run_editor_audit(
    *,
    chapter_no: int,
    front_fails: Sequence[str] | None = None,
    mode: str | None = None,
) -> bool:
    """是否发起责编 LLM 调用。

    默认 ``sample``：首章 + 每 5 章抽一次；前置硬门已命中时跳过（稿未过硬门）。
    ``NOVEL_EDITOR_AUDIT``=``off``|``sample``|``always``。
    """
    import os

    resolved = (mode or os.environ.get("NOVEL_EDITOR_AUDIT") or "sample").strip().lower()
    if resolved in {"0", "false", "off", "never"}:
        return False
    if resolved in {"1", "true", "always", "on"}:
        return True
    if front_fails:
        return False
    n = int(chapter_no)
    return n == 1 or n % 5 == 0


def editor_system_prompt(*, chapter_no: int = 1) -> str:
    _ = chapter_no
    return (
        "你是网文校稿编辑，不是导演、不是事实对账员、不是文风打分器。\n"
        "只做校稿向检查：通顺、重复、读者可读、同章自洽提示。禁止综合分；禁止改写正文。\n"
        "所有 issues 必须 hard=false（顾问提示）；明显人称/宿主壳/操作员代号/题文错名/"
        "金手指完全未外显，应由 VALIDATE 前置拦截，你不要标 hard。\n"
        "检查项（rule_id 必须用下列之一，一律 soft）：\n"
        "- jargon_first_gloss：发明术语附近释义偏弱（提示即可）\n"
        "- power_readable：金手指表述还可更清楚（提示即可）\n"
        "- intra_chapter_contradiction：同章小矛盾提示\n"
        "- title_cast_mismatch：题文人名可再对齐（提示即可）\n"
        "- closed_set_without_scope：名单限定可更清楚\n"
        "- info_debt：信息交代可补\n"
        "- redundant_beats：同章信息复读可剪\n"
        "- causal_unlink：选择与困境挂钩可更显\n"
        "- metaphor_overuse：隐喻可收敛\n"
        "- contract_invisible：读者契约触达偏弱\n"
        "每条问题必须有 quote（当前章正文原句）与 reason；hard 一律 false。\n"
        "无问题则 issues 空、passed=true。\n"
        "payload 里的 power_system / reader_contract / cast / title 是对照材料，"
        "不是正文；不得把对照材料里的句子当成 quote。\n"
        "不要重复 commons 器物 UX；不要重复 VALIDATE 已管的兑现/人称/前置硬门。"
    )


def format_editor_issue(rule_id: str, reason: str, quote: str, *, hard: bool) -> str:
    tag = "editor" if hard else "editor-soft"
    snip = (quote or "").replace("\n", " ")[:80]
    return f"[{tag}:{rule_id}] {reason}｜摘录「{snip}」"


def issues_from_editor_result(
    result: EditorAuditResult,
    *,
    allowed_rule_ids: Sequence[str] | None = None,
    hard_only: bool = True,
    soft_only: bool = False,
    chapter: str = "",
) -> list[str]:
    allowed = set(allowed_rule_ids) if allowed_rule_ids is not None else set(EDITOR_RULE_IDS)
    out: list[str] = []
    for item in result.issues:
        if item.rule_id not in allowed:
            continue
        hard = bool(item.hard)
        if item.rule_id in _FORCE_SOFT_RULES:
            hard = False
        # 术语附近已有功能说明时，不得硬拦
        if hard and item.rule_id in {"jargon_first_gloss", "power_readable"} and chapter:
            if _term_already_glossed(item.quote, chapter):
                hard = False
        if soft_only and hard:
            continue
        if hard_only and not soft_only and not hard:
            continue
        out.append(
            format_editor_issue(item.rule_id, item.reason, item.quote, hard=hard)
        )
    return out


def _term_already_glossed(quote: str, chapter: str) -> bool:
    """摘录附近若已有功能说明/答疑，视为已释义。"""
    text = chapter or ""
    q = (quote or "").strip()
    if not q:
        return False
    idx = text.find(q[:24]) if len(q) >= 8 else text.find(q)
    if idx < 0:
        idx = 0
    window = text[max(0, idx - 80) : idx + min(len(text), 360)]
    gloss_cues = (
        "决定",
        "决定。",
        "是指",
        "意思是",
        "用来",
        "消耗",
        "代价",
        "不能",
        "七天",
        "抽一次",
        "够抽",
        "互动质量",
        "成员",
        "浓度",
        "冷却",
        "变钱",
        "变资源",
    )
    return any(c in window for c in gloss_cues)


def editor_payload(
    *,
    chapter: str,
    chapter_no: int,
    title: str,
    cast: dict[str, Any] | list[str] | None,
    power_system: str = "",
    reader_contract: dict[str, Any] | None = None,
    dramatic_engine: dict[str, Any] | None = None,
    prose_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(cast, dict):
        cast_names = list(cast.keys())
    else:
        cast_names = [str(x) for x in (cast or [])]
    return {
        "chapter_no": int(chapter_no),
        "title": title or "",
        "cast_names": cast_names,
        "power_system": power_system or "",
        "reader_contract": reader_contract or {},
        "dramatic_engine": dramatic_engine or {},
        "prose_style": prose_style or {},
        "chapter": chapter,
        "allowed_rule_ids": list(EDITOR_RULE_IDS),
    }
