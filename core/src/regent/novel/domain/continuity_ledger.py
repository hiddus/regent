"""连续性台账（工作包2）。

首轮只落地 b0ce 已出错的子集：道具（位置/持有人/生命周期/分区文字/读取状态）、
资源（消耗条目）、带作用域的一次性事件、人物位置与访问轮次。

台账与既有 working_state 并行：working_state 继续作为自由文本入口供旧链路消费，
台账作为高可信校验层——当两者冲突时，以台账为准阻断。新协议仅对新建作品生效，
旧作品不强制迁移（2.3）。
"""

from __future__ import annotations

from typing import Any

# --- 生命周期 ---

LIFECYCLE_INTACT = "intact"
LIFECYCLE_DAMAGED = "damaged"
LIFECYCLE_DESTROYED = "destroyed"
LIFECYCLE_UNAVAILABLE = "unavailable"

_VALID_LIFECYCLES = {
    LIFECYCLE_INTACT,
    LIFECYCLE_DAMAGED,
    LIFECYCLE_DESTROYED,
    LIFECYCLE_UNAVAILABLE,
}

# 不可逆：destroyed 只能经声明维修回到 damaged/intact，不能直接回 intact
_IRREVERSIBLE_DESTROY = True

# --- 一次性事件类型 ---

EVENT_ABILITY_READ = "ability_read"
EVENT_DISCOVERY = "discovery"
EVENT_DAMAGE = "damage"
EVENT_RESOURCE_CONSUME = "resource_consume"
EVENT_LEAVE = "leave"

# --- 读取类型 ---

READ_ABILITY = "ability"
READ_TEXT = "text"
READ_VIEW = "view"


def empty_ledger() -> dict[str, Any]:
    """空台账初始结构。"""
    return {
        "props": {},
        "resources": {},
        "one_time_events": {},
        "actor_positions": {},
        "time_marker": "",
        "version": 0,
    }


def _prop(
    prop_id: str,
    *,
    name: str = "",
    position: str = "unknown",
    holder: str = "",
    lifecycle: str = LIFECYCLE_INTACT,
    partitions: dict[str, str] | None = None,
    read_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "prop_id": prop_id,
        "name": name or prop_id,
        "position": position,
        "holder": holder,
        "lifecycle": lifecycle,
        "partitions": dict(partitions or {}),
        "read_status": dict(read_status or {}),
    }


def _resource(resource_id: str, *, name: str = "") -> dict[str, Any]:
    return {
        "resource_id": resource_id,
        "name": name or resource_id,
        "consumed": [],
    }


# --- 作用域键生成 ---


def scope_key_ability_read(object_id: str, read_type: str) -> str:
    return f"ability_read:{object_id}:{read_type}"


def scope_key_discovery(actor_id: str, object_id: str) -> str:
    return f"discovery:{actor_id}:{object_id}"


def scope_key_damage(object_id: str) -> str:
    return f"damage:{object_id}"


def scope_key_resource_consume(resource_id: str, entry: str) -> str:
    return f"resource_consume:{resource_id}:{entry}"


def scope_key_leave(actor_id: str, location: str, visit_round: int) -> str:
    return f"leave:{actor_id}:{location}:r{visit_round}"


# --- 约束校验 ---


def check_prop_readable(
    ledger: dict[str, Any], prop_id: str, *, read_type: str = READ_ABILITY
) -> list[str]:
    """校验道具是否可被读取。返回违规列表（空=通过）。"""
    props = ledger.get("props") or {}
    prop = props.get(prop_id)
    if prop is None:
        return []
    errs: list[str] = []
    lc = str(prop.get("lifecycle") or LIFECYCLE_INTACT)
    if lc == LIFECYCLE_DESTROYED:
        errs.append(f"prop_destroyed:{prop_id}")
    if read_type == READ_ABILITY:
        rs = prop.get("read_status") or {}
        ability = rs.get("ability") or {}
        if ability.get("count", 0) >= 1:
            errs.append(f"ability_read_duplicate:{prop_id}")
    return errs


def check_partition_unchanged(
    ledger: dict[str, Any], prop_id: str, partition: str, new_text: str
) -> list[str]:
    """校验分区文字未被无证据覆盖。"""
    props = ledger.get("props") or {}
    prop = props.get(prop_id)
    if prop is None:
        return []
    existing = (prop.get("partitions") or {}).get(partition)
    if existing is None:
        return []
    if existing != new_text:
        return [f"partition_changed:{prop_id}:{partition}"]
    return []


def check_resource_not_double_consumed(
    ledger: dict[str, Any], resource_id: str, entry: str
) -> list[str]:
    """校验同一资源条目未被重复扣除。"""
    resources = ledger.get("resources") or {}
    res = resources.get(resource_id)
    if res is None:
        return []
    for item in res.get("consumed") or []:
        if str(item.get("entry") or "") == entry:
            return [f"resource_double_consume:{resource_id}:{entry}"]
    return []


def check_one_time_event_unique(
    ledger: dict[str, Any], key: str
) -> list[str]:
    """校验一次性事件在作用域内唯一。"""
    events = ledger.get("one_time_events") or {}
    if key in events:
        return [f"one_time_event_duplicate:{key}"]
    return []


def check_leave_once_per_visit(
    ledger: dict[str, Any], actor_id: str, location: str
) -> list[str]:
    """校验同一访问轮次内只离开一次。"""
    positions = ledger.get("actor_positions") or {}
    pos = positions.get(actor_id)
    if pos is None:
        return []
    current_loc = str(pos.get("location") or "")
    if current_loc != location:
        return []
    visit_round = int(pos.get("visit_round") or 1)
    key = scope_key_leave(actor_id, location, visit_round)
    return check_one_time_event_unique(ledger, key)


# --- 增量应用 ---


def apply_delta(
    ledger: dict[str, Any],
    delta: dict[str, Any],
    *,
    scene_id: str = "",
    prose_hash: str = "",
    event_id: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """应用台账增量，返回 (new_ledger, violations)。

    violations 非空时增量未被应用（原子性：要么全通过，要么全不应用）。
    delta 可含：
    - prop_registrations: [{prop_id, name?, position?, holder?}]
    - prop_updates: [{prop_id, position?, holder?, lifecycle?, partition_updates?}]
    - resource_registrations: [{resource_id, name?}]
    - resource_consumptions: [{resource_id, entry, cause_event_id?}]
    - one_time_events: [{scope_key, event_type, object_id?, actor_id?}]
    - actor_position_updates: [{actor_id, location, enter_new_visit?}]
    - time_marker: str
    """
    import copy

    new_ledger = copy.deepcopy(ledger)
    violations: list[str] = []

    for reg in delta.get("prop_registrations") or []:
        pid = str(reg.get("prop_id") or "").strip()
        if not pid:
            continue
        if pid in (new_ledger.get("props") or {}):
            continue
        new_ledger.setdefault("props", {})[pid] = _prop(
            pid,
            name=str(reg.get("name") or pid),
            position=str(reg.get("position") or "unknown"),
            holder=str(reg.get("holder") or ""),
        )

    for upd in delta.get("prop_updates") or []:
        pid = str(upd.get("prop_id") or "").strip()
        if not pid:
            continue
        prop = (new_ledger.get("props") or {}).get(pid)
        if prop is None:
            continue
        if "position" in upd:
            prop["position"] = str(upd["position"])
        if "holder" in upd:
            prop["holder"] = str(upd["holder"])
        if "lifecycle" in upd:
            new_lc = str(upd["lifecycle"])
            old_lc = str(prop.get("lifecycle") or LIFECYCLE_INTACT)
            if new_lc not in _VALID_LIFECYCLES:
                violations.append(f"invalid_lifecycle:{pid}:{new_lc}")
                continue
            if (
                _IRREVERSIBLE_DESTROY
                and old_lc == LIFECYCLE_DESTROYED
                and new_lc == LIFECYCLE_INTACT
            ):
                violations.append(f"destroyed_cannot_restore:{pid}")
                continue
            prop["lifecycle"] = new_lc
        for pkey, pval in (upd.get("partition_updates") or {}).items():
            existing = (prop.get("partitions") or {}).get(pkey)
            if existing is not None and existing != str(pval):
                violations.append(f"partition_changed:{pid}:{pkey}")
            else:
                prop.setdefault("partitions", {})[pkey] = str(pval)

    for reg in delta.get("resource_registrations") or []:
        rid = str(reg.get("resource_id") or "").strip()
        if not rid:
            continue
        if rid in (new_ledger.get("resources") or {}):
            continue
        new_ledger.setdefault("resources", {})[rid] = _resource(
            rid, name=str(reg.get("name") or rid)
        )

    for con in delta.get("resource_consumptions") or []:
        rid = str(con.get("resource_id") or "").strip()
        entry = str(con.get("entry") or "").strip()
        if not rid or not entry:
            continue
        v = check_resource_not_double_consumed(new_ledger, rid, entry)
        if v:
            violations.extend(v)
            continue
        res = (new_ledger.get("resources") or {}).get(rid)
        if res is None:
            new_ledger.setdefault("resources", {})[rid] = _resource(rid)
            res = new_ledger["resources"][rid]
        res.setdefault("consumed", []).append(
            {
                "entry": entry,
                "cause_event_id": str(con.get("cause_event_id") or event_id),
                "scene_id": scene_id,
            }
        )

    for evt in delta.get("one_time_events") or []:
        key = str(evt.get("scope_key") or "").strip()
        if not key:
            etype = str(evt.get("event_type") or "")
            oid = str(evt.get("object_id") or "")
            aid = str(evt.get("actor_id") or "")
            if etype == EVENT_ABILITY_READ:
                key = scope_key_ability_read(oid, "default")
            elif etype == EVENT_DISCOVERY:
                key = scope_key_discovery(aid, oid)
            elif etype == EVENT_DAMAGE:
                key = scope_key_damage(oid)
            else:
                continue
        v = check_one_time_event_unique(new_ledger, key)
        if v:
            violations.extend(v)
            continue
        new_ledger.setdefault("one_time_events", {})[key] = {
            "event_type": str(evt.get("event_type") or ""),
            "object_id": str(evt.get("object_id") or ""),
            "actor_id": str(evt.get("actor_id") or ""),
            "scene_id": scene_id,
            "prose_hash": prose_hash,
        }

    for apu in delta.get("actor_position_updates") or []:
        aid = str(apu.get("actor_id") or "").strip()
        loc = str(apu.get("location") or "").strip()
        if not aid:
            continue
        pos = (new_ledger.get("actor_positions") or {}).get(aid)
        enter_new = bool(apu.get("enter_new_visit"))
        if pos is None:
            new_ledger.setdefault("actor_positions", {})[aid] = {
                "location": loc,
                "visit_round": 1,
                "last_scene": scene_id,
            }
        else:
            if enter_new or pos.get("location") != loc:
                if pos.get("location") != loc:
                    pos["visit_round"] = 1
                else:
                    pos["visit_round"] = int(pos.get("visit_round") or 0) + 1
            pos["location"] = loc
            pos["last_scene"] = scene_id

    if "time_marker" in delta:
        new_ledger["time_marker"] = str(delta["time_marker"])

    if violations:
        return ledger, violations

    new_ledger["version"] = int(new_ledger.get("version") or 0) + 1
    return new_ledger, []


# --- 读取状态更新（能力读取计数） ---


def record_ability_read(
    ledger: dict[str, Any], prop_id: str, *, scene_id: str = ""
) -> tuple[dict[str, Any], list[str]]:
    """记录一次能力读取，返回 (new_ledger, violations)。"""
    import copy

    v = check_prop_readable(ledger, prop_id, read_type=READ_ABILITY)
    if v:
        return ledger, v
    new_ledger = copy.deepcopy(ledger)
    prop = (new_ledger.get("props") or {}).get(prop_id)
    if prop is None:
        return ledger, [f"prop_not_found:{prop_id}"]
    rs = prop.setdefault("read_status", {})
    ability = rs.setdefault("ability", {"count": 0, "last_scene": ""})
    ability["count"] = int(ability.get("count") or 0) + 1
    ability["last_scene"] = scene_id
    new_ledger["version"] = int(new_ledger.get("version") or 0) + 1
    return new_ledger, []


# --- 序列化 ---


def ledger_to_text_summary(ledger: dict[str, Any]) -> str:
    """台账的文本摘要，供 writer/auditor 上下文使用。"""
    if not ledger or not any(ledger.get(k) for k in ("props", "resources", "actor_positions")):
        return ""
    lines: list[str] = []
    props = ledger.get("props") or {}
    if props:
        lines.append("【道具台账】")
        for pid, p in props.items():
            lc = p.get("lifecycle", "intact")
            loc = p.get("position") or "unknown"
            holder = p.get("holder") or ""
            parts = p.get("partitions") or {}
            rs = p.get("read_status") or {}
            ability_count = (rs.get("ability") or {}).get("count", 0)
            line = f"- {p.get('name', pid)}：位置={loc}"
            if holder:
                line += f"，持有人={holder}"
            line += f"，状态={lc}"
            if ability_count:
                line += f"，能力读取次数={ability_count}"
            if parts:
                line += f"，分区={list(parts.keys())}"
            lines.append(line)
    resources = ledger.get("resources") or {}
    if resources:
        lines.append("【资源台账】")
        for rid, r in resources.items():
            consumed = r.get("consumed") or []
            entries = [c.get("entry", "") for c in consumed]
            lines.append(f"- {r.get('name', rid)}：已消耗={entries}")
    positions = ledger.get("actor_positions") or {}
    if positions:
        lines.append("【人物位置】")
        for aid, pos in positions.items():
            lines.append(
                f"- {aid}：{pos.get('location', 'unknown')}（第{pos.get('visit_round', 1)}轮）"
            )
    tm = ledger.get("time_marker") or ""
    if tm:
        lines.append(f"【时间】{tm}")
    return "\n".join(lines)


def ledger_to_working_state(ledger: dict[str, Any]) -> dict[str, str]:
    """将台账投影为 working_state 字典格式（工作包2.2）。

    替代自由文本 state_changes 作为正式入口状态。
    键格式：prop:{id}.{field}、resource:{id}.consumed、actor:{id}.location、time_marker。
    """
    state: dict[str, str] = {}
    if not ledger or not isinstance(ledger, dict):
        return state
    for pid, p in (ledger.get("props") or {}).items():
        if not isinstance(p, dict):
            continue
        if p.get("position"):
            state[f"prop:{pid}.position"] = str(p["position"])
        if p.get("holder"):
            state[f"prop:{pid}.holder"] = str(p["holder"])
        if p.get("lifecycle"):
            state[f"prop:{pid}.lifecycle"] = str(p["lifecycle"])
    for rid, r in (ledger.get("resources") or {}).items():
        if not isinstance(r, dict):
            continue
        consumed = r.get("consumed") or []
        if consumed:
            entries = [str(c.get("entry", "")) for c in consumed if isinstance(c, dict)]
            state[f"resource:{rid}.consumed"] = ",".join(entries)
    for aid, pos in (ledger.get("actor_positions") or {}).items():
        if isinstance(pos, dict) and pos.get("location"):
            state[f"actor:{aid}.location"] = str(pos["location"])
    tm = ledger.get("time_marker") or ""
    if tm:
        state["time_marker"] = str(tm)
    return state


__all__ = [
    "EVENT_ABILITY_READ",
    "EVENT_DAMAGE",
    "EVENT_DISCOVERY",
    "EVENT_LEAVE",
    "EVENT_RESOURCE_CONSUME",
    "LIFECYCLE_DAMAGED",
    "LIFECYCLE_DESTROYED",
    "LIFECYCLE_INTACT",
    "LIFECYCLE_UNAVAILABLE",
    "READ_ABILITY",
    "READ_TEXT",
    "READ_VIEW",
    "apply_delta",
    "check_leave_once_per_visit",
    "check_one_time_event_unique",
    "check_partition_unchanged",
    "check_prop_readable",
    "check_resource_not_double_consumed",
    "empty_ledger",
    "ledger_to_text_summary",
    "ledger_to_working_state",
    "record_ability_read",
    "scope_key_ability_read",
    "scope_key_damage",
    "scope_key_discovery",
    "scope_key_leave",
    "scope_key_resource_consume",
]
