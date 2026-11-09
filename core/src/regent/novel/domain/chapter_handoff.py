"""章末交接与审校故障分层。

交接只保存已接受正文的尾段原文和已接受状态，不另写摘要事实。
下一章能否使用它，只看正文 hash 与尾段是否仍是该正文的后缀。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "chapter_handoff.v1"
TAIL_CHARS = 800

CONTINUATION_DIRECT = "direct"
CONTINUATION_TIME_JUMP = "time_jump"
CONTINUATION_POV_SWITCH = "pov_switch"
CONTINUATION_OPENING = "opening"
CONTINUATION_MODES = (
    CONTINUATION_DIRECT,
    CONTINUATION_TIME_JUMP,
    CONTINUATION_POV_SWITCH,
    CONTINUATION_OPENING,
)
CONTINUATION_WITH_PREVIOUS = (
    CONTINUATION_DIRECT,
    CONTINUATION_TIME_JUMP,
    CONTINUATION_POV_SWITCH,
)

FAULT_EXPRESSION = "expression"
FAULT_SCENE_EVENT = "scene_event"
FAULT_SCENE_PLAN = "scene_plan"
FAULT_CHAPTER_GOAL = "chapter_goal"
FAULT_PRIOR_CHAPTER = "prior_chapter"

_PLAN_MARKERS = (
    "分场",
    "重复事件",
    "事件重复",
    "重演",
    "责任场",
    "时间顺序",
    "场次组织",
    "同一事件",
)
_GOAL_MARKERS = ("章节目标", "本章目标", "节点未完成")
_PRIOR_MARKERS = ("[prior_canon]", "旧章事实", "前章事实冲突", "跨章事实")
_EXPRESSION_CODES = ("redundant", "复读", "语病")


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_chapter_handoff(
    *,
    chapter_no: int,
    content: str,
    actual_state: dict[str, Any] | None = None,
    review_content_hash: str = "",
    continuity_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从最终正文生成交接。尾段必须是正文后缀，摘要不得另造事实。"""
    text = content or ""
    tail = text[-TAIL_CHARS:] if text else ""
    digest = content_hash(text)
    reviewed = str(review_content_hash or "")
    state = dict(actual_state) if isinstance(actual_state, dict) else {}
    result = {
        "schema": SCHEMA,
        "chapter_no": int(chapter_no),
        "content_hash": digest,
        "tail_excerpt": tail,
        "actual_state": state,
        "review_content_hash": reviewed,
        "review_matches_final": bool(reviewed) and reviewed == digest,
    }
    if isinstance(continuity_ledger, dict) and continuity_ledger:
        result["continuity_ledger"] = dict(continuity_ledger)
    return result


def handoff_bound(handoff: dict[str, Any] | None, content: str) -> bool:
    """交接与当前保存正文一致：hash 相同，且尾段仍是该正文后缀。"""
    if not isinstance(handoff, dict) or handoff.get("schema") != SCHEMA:
        return False
    text = content or ""
    if str(handoff.get("content_hash") or "") != content_hash(text):
        return False
    tail = str(handoff.get("tail_excerpt") or "")
    if not text:
        return tail == ""
    return bool(tail) and text.endswith(tail)


def annotate_chapter_handoff(
    content: str,
    generation_context: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    """只把仍绑定正文的交接传给后章。hash 对不上则标 stale，不猜测。"""
    raw = (generation_context or {}).get("chapter_handoff")
    if not isinstance(raw, dict):
        return None, "absent"
    if handoff_bound(raw, content):
        return raw, "bound"
    return None, "stale"


def select_previous_handoff(
    recent_chapters: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """recent_chapters[0] 是紧邻的前一章。未绑定的交接不进入下一章。"""
    if not recent_chapters:
        return None, "absent"
    status = str(recent_chapters[0].get("handoff_status") or "absent")
    handoff = recent_chapters[0].get("chapter_handoff")
    if status == "bound" and isinstance(handoff, dict):
        return handoff, "bound"
    if status == "stale":
        return None, "stale"
    return None, "absent"


def entry_summary_from_handoff(handoff: dict[str, Any]) -> str:
    """首场入口只引用前章尾段原文和已接受状态，不改写成新事实。"""
    tail = str(handoff.get("tail_excerpt") or "")
    state = handoff.get("actual_state")
    if not isinstance(state, dict):
        state = {}
    state_text = json.dumps(state, ensure_ascii=False)[:800]
    return (
        "前章出口（正文尾段原文，禁止改写成新事实，禁止把尾段已完成的动作再演一遍）："
        f"{tail}\n"
        f"前章已接受状态：{state_text}"
    )


def initial_entry_from_previous(
    previous_handoff: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    if isinstance(previous_handoff, dict) and str(previous_handoff.get("tail_excerpt") or "").strip():
        text = entry_summary_from_handoff(previous_handoff)
        return text, [text]
    return "", [""]


def continuation_issues(
    *,
    mode: str,
    transition_note: str,
    has_previous: bool,
) -> list[str]:
    """有前章交接时，下一章必须声明承接方式。时间跳或换视角必须写转换说明。"""
    chosen = (mode or "").strip()
    note = (transition_note or "").strip()
    if not has_previous:
        if chosen and chosen not in CONTINUATION_MODES:
            return [f"continuation_mode_unknown:{chosen}"]
        return []
    if chosen not in CONTINUATION_WITH_PREVIOUS:
        return ["continuation_mode_required"]
    if chosen in {CONTINUATION_TIME_JUMP, CONTINUATION_POV_SWITCH} and not note:
        return ["transition_note_required"]
    return []


def merge_continuation_gaps(
    hard: list[str],
    *,
    mode: str,
    note: str,
    has_previous: bool,
) -> list[str]:
    gaps = continuation_issues(mode=mode, transition_note=note, has_previous=has_previous)
    if not gaps:
        return list(hard)
    return [*list(hard), *gaps]


def classify_review_fault(message: str) -> str:
    """审校意见落到修复层级。未识别的默认是场内事件，避免误把表达问题打回重分场。"""
    text = str(message or "")
    lowered = text.lower()
    if any(marker in text or marker in lowered for marker in _PRIOR_MARKERS):
        return FAULT_PRIOR_CHAPTER
    if any(marker in text for marker in _PLAN_MARKERS):
        return FAULT_SCENE_PLAN
    if any(marker in text for marker in _GOAL_MARKERS):
        return FAULT_CHAPTER_GOAL
    if any(marker in lowered or marker in text for marker in _EXPRESSION_CODES):
        return FAULT_EXPRESSION
    return FAULT_SCENE_EVENT


def review_repair_layer(issues: list[str]) -> str:
    """计划层和章节目标优先于改一场。前章事实不能靠改当前章正文消掉。"""
    classes = [classify_review_fault(item) for item in issues if str(item or "").strip()]
    if not classes:
        return FAULT_SCENE_EVENT
    if FAULT_PRIOR_CHAPTER in classes:
        return FAULT_PRIOR_CHAPTER
    if FAULT_SCENE_PLAN in classes:
        return FAULT_SCENE_PLAN
    if FAULT_CHAPTER_GOAL in classes:
        return FAULT_CHAPTER_GOAL
    if all(item == FAULT_EXPRESSION for item in classes):
        return FAULT_EXPRESSION
    return FAULT_SCENE_EVENT
