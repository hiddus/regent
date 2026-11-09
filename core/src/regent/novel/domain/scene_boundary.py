"""场景边界校验（工作包3.2）。

校验场记报告的实际事件是否越界：
- 本场兑现了排在后场的 occurrence 时拒绝接纳
- 有 occurrence 责任却不报告实际事件时拒绝接纳
- 台账增量里的重大变化必须能对上一条 occurrence，否则视为无归属变化
- 后场事件的描述出现在本场正文摘录，或本场增量改到了后场才负责的对象，也算提前兑现
"""

from __future__ import annotations

from typing import Any

_ABILITY_MARKS = ("能力读取", "读取能力", "用能力读", "读出能力")


def _occurrence_ids(card: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for ref in card.get("event_refs") or []:
        if not isinstance(ref, dict):
            continue
        if str(ref.get("role") or "") != "occurrence":
            continue
        eid = str(ref.get("event_id") or "").strip()
        if eid:
            ids.add(eid)
    return ids


def _delta_has_material_change(delta: dict[str, Any] | None) -> bool:
    if not isinstance(delta, dict):
        return False
    if delta.get("resource_consumptions"):
        return True
    if delta.get("one_time_events"):
        return True
    for upd in delta.get("prop_updates") or []:
        if isinstance(upd, dict) and upd.get("lifecycle"):
            return True
    for move in delta.get("actor_position_updates") or []:
        if isinstance(move, dict) and move.get("location"):
            return True
    return False


def _events_by_id(script_events: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for event in script_events or []:
        if isinstance(event, dict) and str(event.get("event_id") or "").strip():
            found[str(event["event_id"])] = event
    return found


def _later_phrases(
    later_cards: list[dict[str, Any]],
    events_by_id: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for card in later_cards:
        for ref in card.get("event_refs") or []:
            if not isinstance(ref, dict) or str(ref.get("role") or "") != "occurrence":
                continue
            eid = str(ref.get("event_id") or "").strip()
            event = events_by_id.get(eid) or {}
            chunks = [str(event.get("description") or "")]
            chunks.extend(str(alias or "") for alias in (event.get("aliases") or []))
            for chunk in chunks:
                phrase = chunk.strip()
                if eid and len(phrase) >= 6:
                    rows.append((eid, phrase))
    return rows


def check_scene_boundary(
    scene_id: str,
    actual_events: list[dict[str, Any]],
    scene_plan: dict[str, Any],
    ledger: dict[str, Any],
    ledger_delta: dict[str, Any] | None = None,
    *,
    require_events: bool = False,
    script_events: list[dict[str, Any]] | None = None,
) -> list[str]:
    """校验场景边界，返回违规列表。

    提前兑现先按事件编号，再按后场事件描述的完整短语，最后对照台账增量是否改到了后场对象。
    事件编号缺失时，仅当整句与后场 purpose 完全相同才算提前兑现。
    """
    if not isinstance(ledger, dict):
        ledger = {}
    violations: list[str] = []
    cards = [c for c in (scene_plan.get("cards") or []) if isinstance(c, dict)]
    scene_ids_in_order = [str(c.get("scene_id") or "") for c in cards]
    try:
        current_idx = scene_ids_in_order.index(scene_id)
    except ValueError:
        return [f"boundary_unknown_scene:{scene_id}"]

    current = cards[current_idx]
    current_occ = _occurrence_ids(current)
    later_cards = cards[current_idx + 1 :]
    later_occ: set[str] = set()
    later_purposes: list[str] = []
    for later in later_cards:
        later_occ |= _occurrence_ids(later)
        purpose = str(later.get("purpose") or "").strip()
        if purpose:
            later_purposes.append(purpose)
    events_by_id = _events_by_id(script_events)
    later_phrase_rows = _later_phrases(later_cards, events_by_id)

    events = [e for e in actual_events if isinstance(e, dict)]
    if require_events and current_occ and not events:
        violations.append(f"missing_actual_events:{scene_id}")

    reported_occurrence = False
    reported_ids: set[str] = set()
    for evt in events:
        modality = str(evt.get("modality") or "occurrence")
        if modality != "occurrence":
            continue
        reported_occurrence = True
        resp_scene = str(evt.get("responsibility_scene_id") or "")
        if resp_scene and resp_scene != scene_id:
            violations.append(
                "boundary_violation:"
                f"{scene_id}:event_in_{resp_scene}:{evt.get('statement', '')[:40]}"
            )
            continue
        eid = str(evt.get("event_id") or "").strip()
        if eid:
            reported_ids.add(eid)
        if eid and eid in later_occ and eid not in current_occ:
            violations.append(f"premature_occurrence:{scene_id}:belongs_to_later:{eid}")
            continue
        stmt = str(evt.get("statement") or "").strip()
        evidence = str(evt.get("evidence") or "")
        if not eid and stmt and stmt in later_purposes:
            violations.append(f"premature_occurrence:{scene_id}:purpose:{stmt[:40]}")
            continue
        haystack = f"{stmt}\n{evidence}"
        for later_eid, phrase in later_phrase_rows:
            if later_eid in current_occ or later_eid in reported_ids:
                continue
            if phrase in haystack:
                violations.append(f"premature_occurrence:{scene_id}:phrase:{later_eid}")
                break

    _ledger_reserved_early(
        scene_id,
        ledger,
        ledger_delta,
        later_phrase_rows,
        current_occ,
        violations,
    )

    if _delta_has_material_change(ledger_delta) and not reported_occurrence:
        violations.append(f"unlisted_state_change:{scene_id}")
    return violations


def _ledger_reserved_early(
    scene_id: str,
    ledger: dict[str, Any],
    ledger_delta: dict[str, Any] | None,
    later_phrase_rows: list[tuple[str, str]],
    current_occ: set[str],
    violations: list[str],
) -> None:
    """本场增量如果改到了后场 occurrence 描述里的对象，记为提前兑现。"""
    if not isinstance(ledger_delta, dict) or not later_phrase_rows:
        return
    props = ledger.get("props") or {}
    seen: set[str] = set()
    for evt in ledger_delta.get("one_time_events") or []:
        if not isinstance(evt, dict):
            continue
        etype = str(evt.get("event_type") or "")
        if etype not in {"ability_read", "damage", "discovery"}:
            continue
        oid = str(evt.get("object_id") or "").strip()
        prop = props.get(oid) if isinstance(props.get(oid), dict) else {}
        name = str((prop or {}).get("name") or oid).strip()
        if len(name) < 2:
            continue
        for later_eid, phrase in later_phrase_rows:
            if later_eid in current_occ or later_eid in seen:
                continue
            if not _later_phrase_matches(etype, name, phrase):
                continue
            violations.append(f"premature_occurrence:{scene_id}:ledger:{later_eid}")
            seen.add(later_eid)
            break
    for upd in ledger_delta.get("prop_updates") or []:
        if not isinstance(upd, dict) or str(upd.get("lifecycle") or "") != "destroyed":
            continue
        oid = str(upd.get("prop_id") or "").strip()
        prop = props.get(oid) if isinstance(props.get(oid), dict) else {}
        name = str((prop or {}).get("name") or oid).strip()
        if len(name) < 2:
            continue
        for later_eid, phrase in later_phrase_rows:
            if later_eid in current_occ or later_eid in seen:
                continue
            if not _later_phrase_matches("damage", name, phrase):
                continue
            violations.append(f"premature_occurrence:{scene_id}:ledger:{later_eid}")
            seen.add(later_eid)
            break


def _later_phrase_matches(event_type: str, name: str, phrase: str) -> bool:
    if name not in phrase:
        return False
    if event_type == "ability_read":
        return "能力" in phrase or "读取" in phrase
    if event_type == "damage":
        return any(mark in phrase for mark in ("碎", "毁", "裂"))
    if event_type == "discovery":
        return "发现" in phrase
    return False


def audit_cooperation_gaps(
    scene_id: str,
    actual_events: list[dict[str, Any]],
    scene_plan: dict[str, Any],
    ledger_delta: dict[str, Any] | None,
    *,
    require_events: bool,
    state_changes: dict[str, Any] | None = None,
) -> list[str]:
    """模型没把实际事件或台账增量交全时的缺口。只在新协议下追问。"""
    from regent.novel.domain.continuity_ledger import ledger_delta_is_blank

    if not require_events:
        return []
    gaps: list[str] = []
    cards = [c for c in (scene_plan.get("cards") or []) if isinstance(c, dict)]
    scene_ids = [str(c.get("scene_id") or "") for c in cards]
    if scene_id not in scene_ids:
        return [f"boundary_unknown_scene:{scene_id}"]
    events = [e for e in actual_events if isinstance(e, dict)]
    reported = any(str(e.get("modality") or "occurrence") == "occurrence" for e in events)
    claims_change = bool(state_changes) or reported
    if claims_change and ledger_delta_is_blank(ledger_delta):
        gaps.append("missing_ledger_delta")
    blobs: list[str] = []
    for evt in events:
        if str(evt.get("modality") or "occurrence") != "occurrence":
            continue
        blobs.append(str(evt.get("statement") or "") + str(evt.get("evidence") or ""))
    joined = "\n".join(blobs)
    if any(mark in joined for mark in _ABILITY_MARKS):
        recorded = any(
            isinstance(item, dict)
            and str(item.get("event_type") or "") == "ability_read"
            and str(item.get("object_id") or "").strip()
            and str(item.get("read_type") or "ability") in {"", "ability"}
            for item in (ledger_delta or {}).get("one_time_events") or []
        )
        if not recorded:
            gaps.append("ability_read_unrecorded")
    return gaps


__all__ = ["audit_cooperation_gaps", "check_scene_boundary"]
