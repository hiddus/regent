"""剧本事件清单：从选定剧本提取语义事件，分配稳定 ID。

事件身份由 LLM 语义识别，不是 hash 也不是关键词。
合并同义事件（如"老瘸子受刑"和"老瘸子被抽了七鞭"→同一事件）。
标注 new_round 关系（合法的"第二次搜身"vs 非法的重演）。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


class ScriptEvent(BaseModel):
    """剧本中的语义事件。"""

    event_id: str = Field(
        description="稳定语义 ID，如 evt_old_lamei_torture",
        min_length=1,
    )
    description: str = Field(
        description="事件核心描述，如'老瘸子受刑'",
        min_length=1,
    )
    event_type: Literal["occurrence", "consequence", "reference", "new_round"] = Field(
        description="事件类型：occurrence=发生，consequence=后果，reference=回忆/提及，new_round=新一轮"
    )
    new_reason: str = Field(
        default="",
        description="event_type=new_round 时必填：与上一轮的区别（新原因/新目标）",
    )
    participants: list[str] = Field(
        default_factory=list,
        description="参与者列表，如['老瘸子', '赵铁头']",
    )
    location: str = Field(default="", description="事件发生地点")
    time_marker: str = Field(default="", description="时间标记，如'夜间'")
    aliases: list[str] = Field(
        default_factory=list,
        description="同义描述列表，如['老瘸子被抽了七鞭', '鞭子落在老瘸子背上']",
    )


class ScriptEventList(BaseModel):
    """剧本事件清单。"""

    events: list[ScriptEvent] = Field(
        default_factory=list,
        description="剧本中的语义事件列表",
    )


def _build_event_extraction_prompt(script_dict: dict[str, Any]) -> str:
    """构建事件提取的 LLM prompt。"""
    script_text = _format_script_for_extraction(script_dict)
    return f"""你是剧本分析师。从下面的章节剧本中提取所有语义事件。

## 任务

1. 识别剧本中的所有事件（动作、变化、冲突、转折等）
2. 为每个事件分配稳定的语义 ID（格式：evt_<简短描述>，如 evt_old_lamei_torture）
3. 合并同义事件：如果多个描述指的是同一件事（如"老瘸子受刑"和"老瘸子被抽了七鞭"），合并为一个事件，把不同描述放入 aliases
4. 分类事件类型：
   - occurrence：事件发生（默认）
   - consequence：某事件的直接后果
   - reference：回忆、提及过去事件
   - new_round：合法的新一轮（如"第二次搜身"，必须有明确的新原因）
5. 提取参与者、地点、时间标记

## 剧本内容

{script_text}

## 输出要求

输出 JSON，格式：
{{
  "events": [
    {{
      "event_id": "evt_xxx",
      "description": "事件核心描述",
      "event_type": "occurrence|consequence|reference|new_round",
      "new_reason": "仅 new_round 填写：与上一轮的区别",
      "participants": ["角色1", "角色2"],
      "location": "地点",
      "time_marker": "时间",
      "aliases": ["同义描述1", "同义描述2"]
    }}
  ]
}}

注意：
- event_id 必须稳定、可读，用下划线连接
- 同义事件必须合并，不要分成多个
- new_round 必须有明确的新原因，不能只是重复
- 如果事件不明确，宁可漏掉也不要误判
"""


def _format_script_for_extraction(script_dict: dict[str, Any]) -> str:
    """将剧本格式化为可读文本。"""
    lines = []
    
    if script_dict.get("title_line"):
        lines.append(f"标题：{script_dict['title_line']}")
    
    if script_dict.get("start_state"):
        lines.append(f"起始状态：{script_dict['start_state']}")
    
    if script_dict.get("protagonist_want"):
        lines.append(f"主角目标：{script_dict['protagonist_want']}")
    
    if script_dict.get("opposition"):
        lines.append(f"对立面：{script_dict['opposition']}")
    
    if script_dict.get("beats"):
        lines.append("故事节拍：")
        for i, beat in enumerate(script_dict["beats"], 1):
            lines.append(f"  {i}. {beat}")
    
    if script_dict.get("turning_point"):
        lines.append(f"转折点：{script_dict['turning_point']}")
    
    if script_dict.get("cost"):
        lines.append(f"代价：{script_dict['cost']}")
    
    if script_dict.get("end_change"):
        lines.append(f"结束变化：{script_dict['end_change']}")
    
    if script_dict.get("key_dialogue"):
        lines.append("关键对话：")
        for dialogue in script_dict["key_dialogue"]:
            lines.append(f"  - {dialogue}")
    
    if script_dict.get("power_payoff"):
        lines.append(f"金手指兑现：{script_dict['power_payoff']}")
    
    return "\n".join(lines)


def _normalize_event_id(event_id: str) -> str:
    """标准化事件 ID。"""
    # 移除非字母数字字符，转为小写
    normalized = re.sub(r"[^a-z0-9_]", "", event_id.lower())
    # 确保以 evt_ 开头
    if not normalized.startswith("evt_"):
        normalized = f"evt_{normalized}"
    return normalized


def validate_script_events(events: list[ScriptEvent]) -> list[str]:
    """校验事件清单的内部一致性。"""
    errors = []
    
    # 检查 event_id 唯一性
    seen_ids = {}
    for event in events:
        normalized_id = _normalize_event_id(event.event_id)
        if normalized_id in seen_ids:
            errors.append(
                f"事件 ID 重复：{event.event_id} 和 {seen_ids[normalized_id].description}"
            )
        else:
            seen_ids[normalized_id] = event
    
    # 检查 new_round 必须有明确原因
    for event in events:
        if event.event_type == "new_round" and not (event.new_reason or "").strip():
            errors.append(
                f"new_round 事件 {event.event_id} 缺少 new_reason（与上一轮的区别）"
            )
    
    return errors


async def extract_script_events(
    session,
    provider,
    work,
    run,
    production,
    script_dict: dict[str, Any],
) -> ScriptEventList:
    """从选定剧本提取语义事件清单（LLM 驱动）。
    
    走 _call 路径，支持幂等、预算、崩溃恢复。
    """
    from regent.novel.application.directing_calls import _call, _script_scene_command_id
    
    system_prompt = "你是剧本分析师，从章节剧本中提取语义事件清单。"
    user_payload = {
        "script": script_dict,
        "task": "extract_events",
    }
    
    command_id = _script_scene_command_id(
        run, production, production, "EXTRACT_EVENTS"
    )
    
    result = await _call(
        session=session,
        provider=provider,
        work=work,
        run=run,
        production=production,
        schema=ScriptEventList,
        system=system_prompt,
        payload=user_payload,
        purpose="extract_script_events",
        command_id=command_id,
    )
    
    # 标准化 event_id
    for event in result.events:
        event.event_id = _normalize_event_id(event.event_id)
    
    return result


def _fold(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "").lower())


def _event_text_blobs(event: dict[str, Any]) -> list[str]:
    blobs = [str(event.get("description") or "")]
    blobs.extend(str(a or "") for a in (event.get("aliases") or []))
    return [b for b in (_fold(x) for x in blobs) if b]


def _events_look_synonymous(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """同义启发式：较短文本被较长文本包含，且长度接近，才视为同一事件。

    覆盖「老瘸子受刑」vs「老瘸子受刑之后」这类换名。
    新一轮说明里顺带复述上一场，不能因为包含短句就判成重演。
    """
    blobs_a = _event_text_blobs(a)
    blobs_b = _event_text_blobs(b)
    for left in blobs_a:
        for right in blobs_b:
            if not left or not right:
                continue
            short, long = (left, right) if len(left) <= len(right) else (right, left)
            if len(short) < 4:
                continue
            if short in long and len(short) / len(long) >= 0.5:
                return True
    return False


def ground_orphan_event_roles(
    plan_cards: list[dict[str, Any]],
    script_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """没有任何 occurrence 时，把最早一场的 consequence 改成 occurrence。

    清单里的事件常被标成 consequence，分场又只写 role=consequence。
    硬闸要求同 ID 另有一场真正发生，返工就会在「只标后果」上打转并停章。
    回忆类事件保持 reference，不在这里升成发生。
    """
    events_by_id: dict[str, dict[str, Any]] = {}
    for event in script_events:
        event_id = _normalize_event_id(event.get("event_id", ""))
        if event_id:
            events_by_id[event_id] = event

    cards: list[dict[str, Any]] = []
    for card in plan_cards:
        copied = dict(card)
        copied["event_refs"] = [dict(ref) for ref in (card.get("event_refs") or [])]
        cards.append(copied)

    owners: set[str] = set()
    for card in cards:
        for ref in card["event_refs"]:
            event_id = _normalize_event_id(ref.get("event_id", ""))
            if event_id and str(ref.get("role") or "occurrence") == "occurrence":
                owners.add(event_id)

    promoted: set[str] = set()
    for card in cards:
        for ref in card["event_refs"]:
            event_id = _normalize_event_id(ref.get("event_id", ""))
            if not event_id or event_id in owners or event_id in promoted:
                continue
            event_type = str((events_by_id.get(event_id) or {}).get("event_type") or "")
            if event_type == "reference":
                continue
            if str(ref.get("role") or "occurrence") != "consequence":
                continue
            ref["role"] = "occurrence"
            promoted.add(event_id)
    return cards


def collapse_duplicate_occurrences(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 event_id 只保留最先出现的 occurrence，后面的改成 reference。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        row = dict(card)
        refs: list[Any] = []
        for ref in card.get("event_refs") or []:
            if not isinstance(ref, dict):
                refs.append(ref)
                continue
            item = dict(ref)
            eid = str(item.get("event_id") or "").strip()
            if str(item.get("role") or "") == "occurrence" and eid:
                if eid in seen:
                    item["role"] = "reference"
                else:
                    seen.add(eid)
            refs.append(item)
        row["event_refs"] = refs
        out.append(row)
    return out


def demote_replay_consequences(
    cards: list[dict[str, Any]],
    script_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """发生类事件已经有一场 occurrence 时，后场的 consequence 改成 reference。

    consequence 只留给清单里单独列出的后果事件。挂在同一发生事件上会停章。
    """
    independent = {"consequence", "reference", "new_round"}
    event_type: dict[str, str] = {}
    for event in script_events:
        event_id = _normalize_event_id(event.get("event_id", ""))
        if event_id:
            event_type[event_id] = str(event.get("event_type") or "")
    owners: set[str] = set()
    for card in cards:
        if not isinstance(card, dict):
            continue
        for ref in card.get("event_refs") or []:
            if not isinstance(ref, dict):
                continue
            event_id = _normalize_event_id(ref.get("event_id", ""))
            if event_id and str(ref.get("role") or "") == "occurrence":
                owners.add(event_id)
    out: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        row = dict(card)
        refs: list[Any] = []
        for ref in card.get("event_refs") or []:
            if not isinstance(ref, dict):
                refs.append(ref)
                continue
            item = dict(ref)
            event_id = _normalize_event_id(item.get("event_id", ""))
            role = str(item.get("role") or "")
            kind = event_type.get(event_id, "")
            if (
                role == "consequence"
                and event_id in owners
                and kind not in independent
            ):
                item["role"] = "reference"
            refs.append(item)
        row["event_refs"] = refs
        out.append(row)
    return out


def _shared_run(left: str, right: str, limit: int = 12) -> int:
    """两段文字的最长公共子串长度。够用即停。"""
    if not left or not right:
        return 0
    best = 0
    prev = [0] * (len(right) + 1)
    for char in left:
        cur = [0] * (len(right) + 1)
        for index, other in enumerate(right, 1):
            if char == other:
                cur[index] = prev[index - 1] + 1
                if cur[index] > best:
                    best = cur[index]
                    if best >= limit:
                        return best
        prev = cur
    return best


def _plans_action_outside_catalog(
    card: dict[str, Any] | None,
    script_events: list[dict[str, Any]],
) -> bool:
    """责任场没有 occurrence，但节拍写了清单里没有的新行动。

    章末决定、记下名字这类行动经常没有独立 event_id。
    整段复述已有事件描述的，不算新行动。
    """
    if not isinstance(card, dict):
        return False
    passages = [
        str(card.get("purpose") or ""),
        str(card.get("exit_change") or ""),
        str(card.get("core_choice") or ""),
    ]
    for beat in card.get("beats") or []:
        if isinstance(beat, dict):
            passages.append(str(beat.get("text") or ""))
    catalog: list[str] = []
    for event in script_events:
        catalog.append(str(event.get("description") or ""))
        catalog.extend(str(alias) for alias in (event.get("aliases") or []))
    catalog = [item.strip() for item in catalog if str(item).strip()]
    for passage in passages:
        text = passage.strip()
        if len(text) < 24:
            continue
        if any(_shared_run(text, item) >= 12 for item in catalog):
            continue
        return True
    return False


def validate_plan_event_refs(
    plan_cards: list[dict[str, Any]],
    script_events: list[dict[str, Any]],
    acceptance_items: list[dict[str, Any]] | None = None,
) -> list[str]:
    """三条硬闸：清单完备、唯一所有权、义务落场。外加角色封死与同义 ID。

    返回错误列表，空列表表示通过。
    """
    errors = []

    # 构建 script_events 的 event_id 集合
    events_by_id: dict[str, dict[str, Any]] = {}
    for e in script_events:
        eid = _normalize_event_id(e.get("event_id", ""))
        if eid:
            events_by_id[eid] = e

    valid_event_ids = set(events_by_id)

    # 跟踪每个 event_id 的 occurrence 场景
    occurrence_owners: dict[str, str] = {}
    # scene_id -> 是否有 occurrence
    scenes_with_occurrence: dict[str, bool] = {}
    # 角色封死：非 occurrence 只能是 reference，且须在 occurrence 场之后
    scene_order: list[str] = []
    ref_uses: list[tuple[str, str, str]] = []  # (scene_id, event_id, role)

    for card in plan_cards:
        scene_id = str(card.get("scene_id") or "")
        scene_order.append(scene_id)
        event_refs = card.get("event_refs") or []
        scenes_with_occurrence.setdefault(scene_id, False)

        # 硬闸 0：禁止空引用
        if not event_refs:
            errors.append(
                f"场景 {scene_id} 未引用任何剧本事件（event_refs 为空），"
                f"每场至少绑定 1 条 script_events"
            )
            continue

        for ref in event_refs:
            event_id = _normalize_event_id(ref.get("event_id", ""))
            role = str(ref.get("role") or "occurrence")

            # 硬闸 1：清单完备
            if event_id not in valid_event_ids:
                errors.append(
                    f"场景 {scene_id} 引用了不存在的事件 {event_id}，"
                    f"必须来自 script_events 清单"
                )
                continue

            # 角色封死：禁止用非标准 role 洗白（含 new_round 自由串）
            if role not in ("occurrence", "consequence", "reference"):
                errors.append(
                    f"场景 {scene_id} 事件 {event_id} 的 role={role} 非法；"
                    f"只允许 occurrence/consequence/reference"
                )
                continue

            # reference 类事件不得标成 occurrence（会把回忆写成重演）
            _etype = str(events_by_id[event_id].get("event_type") or "")
            if role == "occurrence" and _etype in ("reference",):
                errors.append(
                    f"场景 {scene_id} 将 reference 事件 {event_id} 标为 occurrence；"
                    f"回忆/提及只能 reference，不得在正文重演"
                )
                continue

            # consequence/new_round 必须是独立 event_id，不能挂在同一事件上洗白重演
            if role != "occurrence" and events_by_id[event_id].get(
                "event_type"
            ) not in ("consequence", "reference", "new_round", None, ""):
                # 同一事件的后续场只能 reference（提及/回忆），不能标成 consequence 重演
                if role == "consequence":
                    errors.append(
                        f"场景 {scene_id} 将事件 {event_id} 标为 consequence，"
                        f"但 consequence 须是独立 event_id；同一事件后续场只能 reference"
                    )
                    continue

            ref_uses.append((scene_id, event_id, role))

            # 硬闸 2：唯一所有权
            if role == "occurrence":
                scenes_with_occurrence[scene_id] = True
                if event_id in occurrence_owners:
                    errors.append(
                        f"事件 {event_id} 在场景 {occurrence_owners[event_id]} "
                        f"和 {scene_id} 都作为 occurrence 出现，"
                        f"同一事件只能在一个场景发生"
                    )
                else:
                    occurrence_owners[event_id] = scene_id

    # 角色封死：reference/consequence 只能出现在 occurrence 场之后
    order_index = {sid: i for i, sid in enumerate(scene_order)}
    for scene_id, event_id, role in ref_uses:
        if role == "occurrence":
            continue
        owner = occurrence_owners.get(event_id)
        if owner is None:
            # 剧本 reference 事件是既往/画外事实，本章提及不需 occurrence。
            # 仅 occurrence 类事件才要求「本章有一场真正发生」。
            _t = str((events_by_id.get(event_id) or {}).get("event_type") or "")
            if event_id in valid_event_ids and _t != "reference" and role != "reference":
                errors.append(
                    f"场景 {scene_id} 以 {role} 引用事件 {event_id}，"
                    f"但本章尚无任何场景作为 occurrence 发生该事件"
                )
            continue
        if order_index.get(scene_id, 0) <= order_index.get(owner, 0):
            errors.append(
                f"场景 {scene_id} 以 {role} 引用事件 {event_id}，"
                f"但 occurrence 场是 {owner}；引用只能出现在其后"
            )

    # 同义 ID：不同 event_id 都作为 occurrence 且描述/别名互相包含 → 换名重演
    occ_items = [
        (event_id, owner) for event_id, owner in occurrence_owners.items()
    ]
    for i, (id_a, owner_a) in enumerate(occ_items):
        for id_b, owner_b in occ_items[i + 1 :]:
            if owner_a == owner_b:
                continue
            if _events_look_synonymous(events_by_id[id_a], events_by_id[id_b]):
                errors.append(
                    f"事件 {id_a}（{owner_a}）与 {id_b}（{owner_b}）描述同义，"
                    f"疑似同一故事事件换名重演；请合并为同一 event_id"
                )

    cards_by_id = {str(card.get("scene_id") or ""): card for card in plan_cards}

    # 硬闸 3：义务落场 — required 义务的责任场必须有 occurrence 事件
    for item in acceptance_items or []:
        if not item.get("required"):
            continue
        owners = [str(s) for s in (item.get("scene_ids") or []) if s]
        if len(owners) != 1:
            # 多责任场/未分配由 acceptance_plan_issues 报
            continue
        owner = owners[0]
        if scenes_with_occurrence.get(owner):
            continue
        if _plans_action_outside_catalog(cards_by_id.get(owner), script_events):
            continue
        errors.append(
            f"义务 {item.get('item_id')} 责任场 {owner} 没有 occurrence 事件，"
            f"义务无法落场；请把该义务绑定到事件实际发生的场景"
        )

    return errors


def _build_repartition_prompt(
    plan_cards: list[dict[str, Any]],
    event_errors: list[str],
    script_events: list[dict[str, Any]],
) -> str:
    """构建重分场的 LLM prompt。"""
    plan_text = _format_plan_for_repartition(plan_cards)
    events_text = _format_events_for_repartition(script_events)
    errors_text = "\n".join(f"- {e}" for e in event_errors)
    
    return f"""你是总导演，负责修复分场计划中的事件冲突。

## 当前分场计划

{plan_text}

## 剧本事件清单

{events_text}

## 检测到的错误

{errors_text}

## 修复要求

1. 识别重复事件：如果同一事件在多个场景作为 occurrence 出现，决定保留哪个场景的版本，其后改成 reference
2. 若错误是「尚无任何场景作为 occurrence」：在最早引用该事件的场把 role 改成 occurrence。不要把唯一的发生场改成 consequence
3. 保持故事连贯性：确保修改后的计划仍然能讲通故事
4. 禁止自造事件：所有 event_id 必须来自剧本事件清单
5. 输出完整的重分场后计划（JSON 格式）

输出格式：
{{
  "cards": [
    {{
      "scene_id": "s1",
      "purpose": "...",
      "event_refs": [
        {{"event_id": "evt_xxx", "beat_id": "b1", "role": "occurrence"}}
      ],
      ...
    }}
  ]
}}
"""


def _format_plan_for_repartition(plan_cards: list[dict[str, Any]]) -> str:
    """将计划格式化为可读文本。"""
    lines = []
    for card in plan_cards:
        lines.append(f"场景 {card.get('scene_id', '?')}:")
        lines.append(f"  目的: {card.get('purpose', '')}")
        event_refs = card.get("event_refs") or []
        if event_refs:
            lines.append("  事件引用:")
            for ref in event_refs:
                lines.append(
                    f"    - {ref.get('event_id', '?')} "
                    f"(beat={ref.get('beat_id', '?')}, role={ref.get('role', 'occurrence')})"
                )
        lines.append("")
    return "\n".join(lines)


def _format_events_for_repartition(script_events: list[dict[str, Any]]) -> str:
    """将事件清单格式化为可读文本。"""
    lines = []
    for event in script_events:
        lines.append(
            f"- {event.get('event_id', '?')}: {event.get('description', '')} "
            f"(type={event.get('event_type', 'occurrence')})"
        )
    return "\n".join(lines)


async def repartition_plan(
    session,
    provider,
    work,
    run,
    production,
    plan_cards: list[dict[str, Any]],
    event_errors: list[str],
    script_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """LLM 驱动的有范围重分场。
    
    当硬闸校验失败时调用，修复事件冲突。
    """
    from pydantic import BaseModel, Field
    from regent.novel.application.directing_calls import _call, _script_scene_command_id
    from regent.novel.domain.scene_card import SceneCard
    
    class RepartitionedPlan(BaseModel):
        cards: list[SceneCard] = Field(default_factory=list)
    
    system_prompt = "你是总导演，负责修复分场计划中的事件冲突。"
    user_payload = {
        "plan_cards": plan_cards,
        "event_errors": event_errors,
        "script_events": script_events,
        "task": "repartition_plan",
    }
    
    command_id = _script_scene_command_id(
        run, production, production, "REPARTITION_PLAN"
    )
    
    result = await _call(
        session=session,
        provider=provider,
        work=work,
        run=run,
        production=production,
        schema=RepartitionedPlan,
        system=system_prompt,
        payload=user_payload,
        purpose="repartition_plan",
        command_id=command_id,
    )
    
    return [card.model_dump() for card in result.cards]
