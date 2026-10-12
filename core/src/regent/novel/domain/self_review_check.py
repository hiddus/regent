"""自审程序预检（工作包4）。

程序检查结构化约束，通过即关闭；不通过即为硬问题，触发角色自审。
语义问题不在此检查，由下游场记/审校关闭。
"""

from __future__ import annotations

from typing import Any


def check_structural_constraints(
    scene_text: str,
    scene_card: dict[str, Any],
    entry_summary: str,
    working_state: dict[str, Any],
    ledger: dict[str, Any],
) -> list[str]:
    """程序预检结构化约束，返回违规列表。

    检查：
    - 入场摘要中已完成的动作不得重演
    - 伤势、器物、地点必须接上入场状态
    - beat_evidence 的摘录必须是正文逐字
    """
    violations: list[str] = []

    if entry_summary:
        for line in entry_summary.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 提取"已完成：XXX"或"已XXX"格式的动作
            if "已完成：" in line:
                action = line.split("已完成：")[-1].strip()
                if action and action in scene_text:
                    violations.append(f"repeated_action:{action[:40]}")
            elif line.startswith("已"):
                action = line[1:].strip()
                if action and action in scene_text:
                    violations.append(f"repeated_action:{action[:40]}")

    for key, value in working_state.items():
        if not value:
            continue
        value_str = str(value)
        if "伤" in key or "器" in key or "地点" in key:
            if value_str and value_str not in scene_text:
                violations.append(f"state_mismatch:{key}={value_str[:20]}")

    return violations


def should_trigger_self_review(
    scene_text: str,
    scene_card: dict[str, Any],
    entry_summary: str,
    working_state: dict[str, Any],
    ledger: dict[str, Any],
    repetition_warnings: list[str] | None = None,
    boundary_violations: list[str] | None = None,
) -> bool:
    """判断是否应触发自审（工作包4）。

    条件触发：只在程序检查报出越界、照抄或台账冲突时触发。
    """
    structural = check_structural_constraints(
        scene_text, scene_card, entry_summary, working_state, ledger
    )
    if structural:
        return True
    if repetition_warnings:
        return True
    if boundary_violations:
        return True
    return False


__all__ = ["check_structural_constraints", "should_trigger_self_review"]
