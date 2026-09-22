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
        if event.event_type == "new_round" and not event.aliases:
            # new_round 应该有 aliases 说明与上一轮的区别
            errors.append(
                f"new_round 事件 {event.event_id} 缺少与上一轮的区别说明"
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


def validate_plan_event_refs(
    plan_cards: list[dict[str, Any]],
    script_events: list[dict[str, Any]],
) -> list[str]:
    """三条硬闸校验：清单完备、唯一所有权、义务落场。
    
    返回错误列表，空列表表示通过。
    """
    errors = []
    
    # 构建 script_events 的 event_id 集合
    valid_event_ids = {
        _normalize_event_id(e.get("event_id", ""))
        for e in script_events
        if e.get("event_id")
    }
    
    # 跟踪每个 event_id 的 occurrence 场景
    occurrence_owners: dict[str, str] = {}
    
    for card in plan_cards:
        scene_id = card.get("scene_id", "")
        event_refs = card.get("event_refs") or []
        
        for ref in event_refs:
            event_id = _normalize_event_id(ref.get("event_id", ""))
            role = ref.get("role", "occurrence")
            
            # 硬闸 1：清单完备
            if event_id not in valid_event_ids:
                errors.append(
                    f"场景 {scene_id} 引用了不存在的事件 {event_id}，"
                    f"必须来自 script_events 清单"
                )
                continue
            
            # 硬闸 2：唯一所有权
            if role == "occurrence":
                if event_id in occurrence_owners:
                    errors.append(
                        f"事件 {event_id} 在场景 {occurrence_owners[event_id]} "
                        f"和 {scene_id} 都作为 occurrence 出现，"
                        f"同一事件只能在一个场景发生"
                    )
                else:
                    occurrence_owners[event_id] = scene_id
    
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

1. 识别重复事件：如果同一事件在多个场景作为 occurrence 出现，决定保留哪个场景的版本
2. 调整受影响场景：将重复事件的 role 改为 consequence 或 reference，或从该场景删除
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
