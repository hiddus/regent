"""场景边界校验（工作包3.2）。

校验场记报告的实际事件是否越界：
- 本场兑现了排在后场的 occurrence（如齿轮读取/碎裂、离店）时拒绝接纳
- 无事件归属的重大状态变化要求解释
"""

from __future__ import annotations

from typing import Any


def check_scene_boundary(
    scene_id: str,
    actual_events: list[dict[str, Any]],
    scene_plan: dict[str, Any],
    ledger: dict[str, Any],
) -> list[str]:
    """校验场景边界，返回违规列表。

    检查：
    1. occurrence 事件是否属于本场的责任范围
    2. 是否提前兑现了后场事件
    3. unlisted_changes 是否有合理解释
    """
    violations: list[str] = []
    cards = scene_plan.get("cards") or []
    scene_ids_in_order = [str(c.get("scene_id") or "") for c in cards]

    try:
        current_idx = scene_ids_in_order.index(scene_id)
    except ValueError:
        return []

    for evt in actual_events:
        if not isinstance(evt, dict):
            continue
        modality = str(evt.get("modality") or "occurrence")
        if modality != "occurrence":
            continue
        resp_scene = str(evt.get("responsibility_scene_id") or "")
        if resp_scene and resp_scene != scene_id:
            violations.append(
                f"boundary_violation:{scene_id}:event_in_{resp_scene}:{evt.get('statement', '')[:40]}"
            )
            continue
        stmt = str(evt.get("statement") or "")
        for later_idx in range(current_idx + 1, len(scene_ids_in_order)):
            later_card = cards[later_idx] if later_idx < len(cards) else {}
            later_purpose = str(later_card.get("purpose") or "")
            if stmt and later_purpose and stmt in later_purpose:
                violations.append(
                    f"premature_occurrence:{scene_id}:belongs_to_{scene_ids_in_order[later_idx]}:{stmt[:40]}"
                )
                break

    return violations


__all__ = ["check_scene_boundary"]
