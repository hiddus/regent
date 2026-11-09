"""作品内容指纹与用户态投影（纯规则）。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from regent.novel.domain.models import UXProjection
from regent.novel.domain.states import StoryWorkState


def fingerprint(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def next_milestone(key: StoryWorkState, pending: int) -> str | None:
    if pending:
        return "完成裁决"
    if key == StoryWorkState.RUNNING:
        return "下一章"
    return None


def projection_for(
    state: str,
    *,
    pending: int = 0,
    chapter_no: int | None = None,
    pending_kind: str | None = None,
) -> UXProjection:
    known = {
        StoryWorkState.ONBOARDING: ("confirm_direction", "确认故事方向", ["confirm_direction"]),
        StoryWorkState.READY: ("ready_to_start", "可以开始了", ["start_run"]),
        StoryWorkState.RUNNING: ("writing", "正在写下一章", ["leave_safely", "pause"]),
        StoryWorkState.PENDING_DECISION: (
            "needs_your_call",
            "有一个选择需要你定",
            ["open_decision"],
        ),
        StoryWorkState.PAUSED_QUOTA: ("paused_quota", "额度用完了，已暂停", ["top_up"]),
        StoryWorkState.PAUSED_COST: ("paused_cost", "成本达到上限，已暂停", ["raise_limit"]),
        StoryWorkState.RECOMPUTING: ("adjusting", "正在按你的改动重算", ["leave_safely"]),
        StoryWorkState.FAILED: (
            "recoverable_problem",
            "上一章失败，可查看原因后重新生成",
            ["resume", "regenerate_chapter"],
        ),
        StoryWorkState.DONE: ("volume_done", "本卷完成", ["start_next_volume", "expand_volume"]),
        StoryWorkState.CANCELLED: ("cancelled", "已取消", []),
        StoryWorkState.ARCHIVED: ("archived", "已归档", []),
    }
    try:
        key = StoryWorkState(state)
    except ValueError:
        return UXProjection(
            public_stage="unknown_recoverable",
            stage_label="状态同步中",
            safe_to_leave=True,
            unknown_recoverable=True,
            available_actions=["reload"],
        )
    stage, label, actions = known[key]
    # 卷末待确认：不是普通剧情裁决，动作应对齐 expand / 终局意图
    if key == StoryWorkState.PENDING_DECISION and pending_kind == "volume_expansion":
        stage = "volume_expansion_pending"
        label = "本卷已写完，是否扩下一卷？"
        actions = ["expand_volume", "set_ending_intent"]
    return UXProjection(
        public_stage=stage,
        stage_label=label,
        last_completed_artifact=(f"第 {chapter_no} 章" if chapter_no else None),
        next_milestone=next_milestone(key, pending),
        eta_range={"min_minutes": 3, "max_minutes": 15} if key == StoryWorkState.RUNNING else None,
        safe_to_leave=True,
        stale_at=datetime.now(UTC) + timedelta(minutes=10),
        action_required=pending > 0 or key == StoryWorkState.PENDING_DECISION,
        available_actions=actions,
    )


_fingerprint = fingerprint
_next_milestone = next_milestone
_projection_for = projection_for
