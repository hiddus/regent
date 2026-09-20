"""整章硬失败 → 分场修订目标定位（确定性，无模型）。

VALIDATE/ACCEPT 不得默认修末场：能定位则回退最早受影响场；
无法定位则显式升级停机。无效 scene_id / 缺失下标不得默认首场。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

_SNIP_RE = re.compile(r"摘录「([^」]+)」")
_CODE_RE = re.compile(r"^\[([a-z0-9_:-]+)\]")
_MIN_PARA = 32


@dataclass(frozen=True)
class LocatedIssue:
    issue_id: str
    code: str
    severity: Literal["hard", "soft"] = "hard"
    scene_ids: tuple[str, ...] = ()
    evidence_quotes: tuple[str, ...] = ()
    content_hash: str = ""
    expected_action: str = "rewrite_scene"
    verify_rule: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "code": self.code,
            "severity": self.severity,
            "scene_ids": list(self.scene_ids),
            "evidence_quotes": list(self.evidence_quotes),
            "content_hash": self.content_hash,
            "expected_action": self.expected_action,
            "verify_rule": self.verify_rule,
            "message": self.message,
        }


@dataclass(frozen=True)
class RepairTarget:
    """选定的局部修订目标。"""

    scene_index: int
    scene_id: str
    scene_ids: tuple[str, ...]
    issues: tuple[LocatedIssue, ...]
    instruction: str
    locatable: bool = True


@dataclass
class RedundantHit:
    quote: str
    para_index_a: int
    para_index_b: int
    kind: str  # identical | near


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _long_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text or "") if len(p.strip()) >= _MIN_PARA]


def find_near_duplicate_paragraphs(text: str) -> list[RedundantHit]:
    """与 front_gate 复读规则一致：返回首组重复及段落下标。"""
    paras = _long_paragraphs(text)
    if len(paras) < 2:
        return []
    for i, a in enumerate(paras):
        for j, b in enumerate(paras[i + 1 :], start=i + 1):
            if a == b:
                return [
                    RedundantHit(
                        quote=a[:120],
                        para_index_a=i,
                        para_index_b=j,
                        kind="identical",
                    )
                ]
            short, long = (a, b) if len(a) <= len(b) else (b, a)
            if len(short) >= 48 and short in long:
                return [
                    RedundantHit(
                        quote=short[:120],
                        para_index_a=i,
                        para_index_b=j,
                        kind="near",
                    )
                ]
    return []


def _scene_id_at(cards: list[Any], index: int) -> str:
    if 0 <= index < len(cards):
        card = cards[index]
        if isinstance(card, dict):
            return str(card.get("scene_id") or f"i{index}")
        sid = getattr(card, "scene_id", None)
        if sid:
            return str(sid)
    return f"i{index}"


def _known_scene_ids(cards: list[Any]) -> set[str]:
    return {_scene_id_at(cards, i) for i in range(len(cards))} if cards else set()


def _resolve_scene_index(sid: str, cards: list[Any]) -> int | None:
    """仅当 scene_id 属于当前卡集合时返回下标；否则 None。"""
    known = _known_scene_ids(cards)
    if sid not in known:
        # 允许 i{n} 仅当 cards 为空时作为占位；有卡则必须命中卡集合
        if not cards and sid.startswith("i") and sid[1:].isdigit():
            return int(sid[1:])
        return None
    for i in range(len(cards)):
        if _scene_id_at(cards, i) == sid:
            return i
    return None


def _paragraph_scene_map(scene_texts: list[str]) -> list[int]:
    """整章按空行切段后，每段归属的 scene_index。

    与 ``find_near_duplicate_paragraphs`` / ``_long_paragraphs`` **共用同一规则**：
    只映射长度≥32 的段；**不**为空场插入虚拟槽（避免短场导致索引偏移）。
    """
    mapping: list[int] = []
    for scene_i, text in enumerate(scene_texts):
        for _ in _long_paragraphs(text):
            mapping.append(scene_i)
    return mapping


def _chapter_from_scenes(scene_texts: list[str]) -> str:
    """拼接整章正文：只连接各场长段，与段落映射同源。"""
    parts: list[str] = []
    for text in scene_texts:
        parts.extend(_long_paragraphs(text))
    return "\n\n".join(parts)


def _extract_snip(message: str) -> str:
    m = _SNIP_RE.search(message or "")
    return (m.group(1) if m else "").strip()


def _extract_code(message: str) -> str:
    m = _CODE_RE.match((message or "").strip())
    if m:
        return m.group(1)
    if "redundant" in (message or ""):
        return "front:redundant"
    return "unknown"


def _issue_id(code: str, message: str, quote: str) -> str:
    digest = hashlib.sha256(f"{code}|{quote}|{message[:80]}".encode()).hexdigest()[:12]
    return f"{code}#{digest}"


def _is_redundant_code(code: str) -> bool:
    return "redundant" in (code or "")


def scenes_containing_quote(scene_texts: list[str], quote: str) -> list[int]:
    q = (quote or "").strip()
    if not q:
        return []
    hits: list[int] = []
    for i, text in enumerate(scene_texts):
        if q in (text or ""):
            hits.append(i)
    return hits


def locate_fail_messages(
    fails: list[str],
    *,
    scene_texts: list[str],
    cards: list[Any] | None = None,
    chapter_content: str = "",
) -> list[LocatedIssue]:
    """把硬失败字符串映射到 scene_id；无法归属时 scene_ids 为空。"""
    cards = list(cards or [])
    texts = [str(t or "") for t in scene_texts]
    # 优先用场文本同源拼接，避免外部分段与映射不一致
    chapter = _chapter_from_scenes(texts) or chapter_content or "\n\n".join(
        t for t in texts if t.strip()
    )
    ch_hash = content_hash(chapter)
    para_map = _paragraph_scene_map(texts)
    out: list[LocatedIssue] = []

    for raw in fails:
        msg = str(raw or "").strip()
        if not msg:
            continue
        code = _extract_code(msg)
        quotes: list[str] = []
        scene_indices: list[int] = []

        snip = _extract_snip(msg)
        if snip:
            quotes.append(snip)
            scene_indices.extend(scenes_containing_quote(texts, snip))

        if _is_redundant_code(code):
            hits = find_near_duplicate_paragraphs(chapter)
            if hits:
                hit = hits[0]
                if hit.quote and hit.quote not in quotes:
                    quotes.append(hit.quote)
                for pi in (hit.para_index_a, hit.para_index_b):
                    if 0 <= pi < len(para_map):
                        scene_indices.append(para_map[pi])
                    else:
                        scene_indices.extend(scenes_containing_quote(texts, hit.quote[:48]))

        uniq_idx = list(dict.fromkeys(i for i in scene_indices if i >= 0))
        scene_ids = tuple(_scene_id_at(cards, i) for i in uniq_idx)
        if _is_redundant_code(code):
            expected = "remove_dup" if scene_ids else "escalate_nolocal"
            verify = (
                f"摘录不得再重复出现：{quotes[0][:36]}" if quotes else "重复段落须去重"
            )
        else:
            expected = "rewrite_scene" if scene_ids else "escalate_nolocal"
            verify = (
                f"纠正后不得再成立错误断言；证据：{quotes[0][:36]}"
                if quotes
                else "纠正问题所述事实冲突"
            )
        out.append(
            LocatedIssue(
                issue_id=_issue_id(code, msg, quotes[0] if quotes else msg),
                code=code,
                severity="hard",
                scene_ids=scene_ids,
                evidence_quotes=tuple(quotes),
                content_hash=ch_hash,
                expected_action=expected,
                verify_rule=verify,
                message=msg,
            )
        )
    return out


def format_revision_instruction(
    *,
    prefix: str,
    issues: list[LocatedIssue],
    scene_ids: tuple[str, ...] = (),
    max_notes: int = 3,
) -> str:
    """结构化修订指令：按动作类型分开证据用途，禁止事实问题套去重模板。"""
    parts: list[str] = [prefix.rstrip("：:；; ") + "："]
    if len(scene_ids) > 1:
        parts.append(f"涉及场 {','.join(scene_ids)}；只改最早场，后续场将重拍。")
    actions = list(dict.fromkeys(i.expected_action for i in issues if i.expected_action))
    if actions:
        parts.append(f"动作={'/'.join(actions)}。")

    dup_quotes = [
        q
        for i in issues
        if i.expected_action == "remove_dup"
        for q in i.evidence_quotes
        if q
    ]
    fact_quotes = [
        q
        for i in issues
        if i.expected_action != "remove_dup"
        for q in i.evidence_quotes
        if q
    ]
    if dup_quotes:
        uniq_q = list(dict.fromkeys(dup_quotes))[:2]
        parts.append("必须删除或改写以下重复摘录（不得原样保留两处）：")
        for q in uniq_q:
            parts.append(f"「{q[:48]}」")
    if fact_quotes:
        uniq_e = list(dict.fromkeys(fact_quotes))[:2]
        parts.append("问题证据（须消除错误断言，不是简单删句）：")
        for q in uniq_e:
            parts.append(f"「{q[:48]}」")

    notes = "；".join((i.message if i.message else i.issue_id) for i in issues[:max_notes])
    if notes:
        parts.append(f"问题：{notes}")
    parts.append("保留本场已正确的新信息与节拍；禁止整段复读已成立事实。")
    return "".join(parts)[:400]


def repair_target_from_scene_index(
    *,
    scene_index: int | None,
    cards: list[Any],
    issues: list[LocatedIssue] | None = None,
    instruction_prefix: str,
) -> RepairTarget:
    """兼容回退：仅有合法 failed_scene_index 时构造修订目标。

    缺失 / 负值 / 越界 → locatable=False，禁止夹取到首场或末场。
    """
    if scene_index is None or int(scene_index) < 0:
        return RepairTarget(
            scene_index=-1,
            scene_id="",
            scene_ids=(),
            issues=tuple(issues or ()),
            instruction=f"{instruction_prefix}无法局部定位：缺少合法场景下标"[:400],
            locatable=False,
        )
    idx = int(scene_index)
    if not cards or idx >= len(cards):
        return RepairTarget(
            scene_index=-1,
            scene_id="",
            scene_ids=(),
            issues=tuple(issues or ()),
            instruction=(
                f"{instruction_prefix}无法局部定位：场景下标越界 "
                f"index={idx} cards={len(cards)}"
            )[:400],
            locatable=False,
        )
    sid = _scene_id_at(cards, idx)
    located = list(issues or [])
    if not located:
        located = [
            LocatedIssue(
                issue_id=_issue_id("review", instruction_prefix, sid),
                code="review",
                severity="hard",
                scene_ids=(sid,),
                expected_action="rewrite_scene",
                message=instruction_prefix,
            )
        ]
    return RepairTarget(
        scene_index=idx,
        scene_id=sid,
        scene_ids=(sid,),
        issues=tuple(located),
        instruction=format_revision_instruction(
            prefix=instruction_prefix, issues=located, scene_ids=(sid,)
        ),
        locatable=True,
    )


def _filter_valid_scene_ids(
    issue: LocatedIssue, *, cards: list[Any]
) -> LocatedIssue:
    """丢掉不属于当前场景卡集合的 ID。"""
    if not issue.scene_ids:
        return issue
    valid: list[str] = []
    for sid in issue.scene_ids:
        if _resolve_scene_index(str(sid), cards) is not None:
            valid.append(str(sid))
    if tuple(valid) == issue.scene_ids:
        return issue
    expected = issue.expected_action
    if not valid and expected != "escalate_nolocal":
        expected = "escalate_nolocal"
    return LocatedIssue(
        issue_id=issue.issue_id,
        code=issue.code,
        severity=issue.severity,
        scene_ids=tuple(valid),
        evidence_quotes=issue.evidence_quotes,
        content_hash=issue.content_hash,
        expected_action=expected,
        verify_rule=issue.verify_rule,
        message=issue.message,
    )


def select_repair_target(
    issues: list[LocatedIssue],
    *,
    cards: list[Any],
    scene_texts: list[str],
    instruction_prefix: str,
    max_notes: int = 3,
) -> RepairTarget:
    """从已定位问题选出修订场；全部无法定位则 locatable=False。

    无效 scene_id 不得回退到首场；混合可定位/不可定位时仍只使用合法 ID，
    但全部 issue 保留在票据 issues 中。
    """
    hard = [i for i in issues if i.severity == "hard"]
    if not hard:
        hard = list(issues)
    validated = [_filter_valid_scene_ids(i, cards=cards) for i in hard]
    locatable = [i for i in validated if i.scene_ids]
    if not locatable:
        notes = "；".join(i.message for i in hard[:max_notes]) or "未知硬失败"
        return RepairTarget(
            scene_index=-1,
            scene_id="",
            scene_ids=(),
            issues=tuple(validated or hard),
            instruction=f"{instruction_prefix}无法局部定位：{notes}"[:400],
            locatable=False,
        )

    earliest: int | None = None
    all_ids: list[str] = []
    for issue in locatable:
        for sid in issue.scene_ids:
            all_ids.append(sid)
            idx = _resolve_scene_index(sid, cards)
            if idx is None:
                continue
            if earliest is None or idx < earliest:
                earliest = idx
    if earliest is None:
        notes = "；".join(i.message for i in hard[:max_notes]) or "未知硬失败"
        return RepairTarget(
            scene_index=-1,
            scene_id="",
            scene_ids=(),
            issues=tuple(validated),
            instruction=f"{instruction_prefix}无法局部定位：{notes}"[:400],
            locatable=False,
        )

    scene_index = earliest
    scene_id = _scene_id_at(cards, scene_index)
    uniq_ids = tuple(dict.fromkeys(all_ids))
    return RepairTarget(
        scene_index=scene_index,
        scene_id=scene_id,
        scene_ids=uniq_ids,
        issues=tuple(validated),  # 含未定位条目，便于票据完整留存
        instruction=format_revision_instruction(
            prefix=instruction_prefix,
            issues=locatable,
            scene_ids=uniq_ids,
            max_notes=max_notes,
        ),
        locatable=True,
    )


def apply_repair_target_to_script_state(
    sp: dict[str, Any],
    target: RepairTarget,
) -> None:
    """按修订目标截断后续场并设置 scene_index / 意见 / 票据。"""
    from copy import deepcopy

    texts = list(sp.get("scene_texts") or [])
    idx = int(target.scene_index)
    if idx < 0:
        raise ValueError("repair target not locatable")
    if idx < len(texts):
        sp["scene_texts"] = texts[: idx + 1]
    trail = list(sp.get("scene_state_trail") or [])
    if trail and idx + 1 <= len(trail):
        sp["working_state"] = deepcopy(trail[idx])
        sp["scene_state_trail"] = trail[: idx + 1]
    elif trail and idx < len(trail):
        sp["working_state"] = deepcopy(trail[idx])
        sp["scene_state_trail"] = trail[: idx + 1]
    sp["scene_index"] = idx
    sp["scene_revision_instruction"] = target.instruction
    codes = list(dict.fromkeys(i.code for i in target.issues if i.code))
    must_remove = [
        q
        for i in target.issues
        if i.expected_action == "remove_dup"
        for q in i.evidence_quotes
        if q
    ]
    evidence_only = [
        q
        for i in target.issues
        if i.expected_action != "remove_dup"
        for q in i.evidence_quotes
        if q
    ]
    sp["pending_repair_ticket"] = {
        "scene_index": idx,
        "scene_id": target.scene_id,
        "scene_ids": list(target.scene_ids),
        "issue_ids": [i.issue_id for i in target.issues],
        "codes": codes,
        "issues": [i.as_dict() for i in target.issues],
        "base_content_hash": target.issues[0].content_hash if target.issues else "",
        "must_remove_quotes": must_remove,
        "evidence_quotes": evidence_only,
        "forbidden_claims": [
            i.message for i in target.issues if i.expected_action == "rewrite_scene" and i.message
        ][:4],
        "verify_rules": [i.verify_rule for i in target.issues if i.verify_rule],
        "expected_actions": [
            i.expected_action for i in target.issues if i.expected_action
        ],
    }


def verify_repair_progress(
    *,
    ticket: dict[str, Any] | None,
    chapter_content: str,
    remaining_fails: list[str],
) -> dict[str, Any]:
    """复检修订票据：优先按 code/摘录，不单靠文案重算 issue_id。"""
    ticket = ticket or {}
    issue_ids = list(ticket.get("issue_ids") or [])
    ticket_codes = {str(c) for c in (ticket.get("codes") or []) if str(c)}
    must_remove = [str(q) for q in (ticket.get("must_remove_quotes") or []) if str(q)]
    remaining_msgs = [str(m) for m in remaining_fails if str(m).strip()]
    remaining_codes = {_extract_code(m) for m in remaining_msgs}
    remaining_ids = set()
    for msg in remaining_msgs:
        code = _extract_code(msg)
        snip = _extract_snip(msg)
        remaining_ids.add(_issue_id(code, msg, snip or msg))

    codes_cleared = bool(ticket_codes) and not (ticket_codes & remaining_codes)
    codes_still = sorted(ticket_codes & remaining_codes) if ticket_codes else []

    solved = [iid for iid in issue_ids if iid not in remaining_ids]
    still_open = [iid for iid in issue_ids if iid in remaining_ids]
    dup_still = [q for q in must_remove if chapter_content.count(q) >= 2]
    quotes_gone = bool(must_remove) and all(
        chapter_content.count(q) < 2 for q in must_remove
    )

    if ticket_codes:
        progress = codes_cleared and not dup_still
    elif issue_ids:
        progress = bool(solved) and not still_open and not dup_still
    else:
        progress = quotes_gone and not remaining_msgs

    return {
        "solved_issue_ids": solved,
        "open_issue_ids": still_open,
        "open_codes": codes_still,
        "duplicate_quotes_remaining": dup_still,
        "quotes_resolved": quotes_gone,
        "progress": progress,
    }


__all__ = [
    "LocatedIssue",
    "RepairTarget",
    "RedundantHit",
    "apply_repair_target_to_script_state",
    "content_hash",
    "find_near_duplicate_paragraphs",
    "format_revision_instruction",
    "locate_fail_messages",
    "repair_target_from_scene_index",
    "scenes_containing_quote",
    "select_repair_target",
    "verify_repair_progress",
]
