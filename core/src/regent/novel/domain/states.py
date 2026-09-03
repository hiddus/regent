"""Novel Engine 状态机（Tech-Spec §3.3）。

所有状态迁移以 expected_version 条件更新实现；不允许静默覆盖。
人工等待与配额暂停必须释放 worker。
"""

from __future__ import annotations

from enum import StrEnum


class StoryWorkState(StrEnum):
    """作品状态机。"""

    ONBOARDING = "ONBOARDING"
    READY = "READY"
    RUNNING = "RUNNING"
    PENDING_DECISION = "PENDING_DECISION"
    PAUSED_QUOTA = "PAUSED_QUOTA"
    PAUSED_COST = "PAUSED_COST"
    RECOMPUTING = "RECOMPUTING"
    FAILED = "FAILED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class ChapterRunState(StrEnum):
    """章节运行状态机。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PENDING_DECISION = "PENDING_DECISION"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    CANONIZED = "CANONIZED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class ChapterStep(StrEnum):
    """章节内确定性步骤顺序（Tech-Spec §4）。"""

    ASSEMBLE = "ASSEMBLE"
    PERFORM = "PERFORM"
    DIRECT = "DIRECT"
    WEAVE = "WEAVE"
    REVIEW = "REVIEW"
    CANON = "CANON"


class StepState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class DecisionState(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ModerationDecision(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPEALED = "APPEALED"
    RESOLVED = "RESOLVED"


CHAPTER_STEP_ORDER: tuple[ChapterStep, ...] = (
    ChapterStep.ASSEMBLE,
    ChapterStep.PERFORM,
    ChapterStep.DIRECT,
    ChapterStep.WEAVE,
    ChapterStep.REVIEW,
    ChapterStep.CANON,
)

# 允许的状态迁移。空集合表示终态。
_STORY_WORK_TRANSITIONS: dict[StoryWorkState, frozenset[StoryWorkState]] = {
    StoryWorkState.ONBOARDING: frozenset(
        {StoryWorkState.READY, StoryWorkState.CANCELLED, StoryWorkState.ARCHIVED}
    ),
    StoryWorkState.READY: frozenset(
        {
            StoryWorkState.RUNNING,
            StoryWorkState.RECOMPUTING,
            StoryWorkState.CANCELLED,
            StoryWorkState.ARCHIVED,
        }
    ),
    StoryWorkState.RUNNING: frozenset(
        {
            StoryWorkState.PENDING_DECISION,
            StoryWorkState.PAUSED_QUOTA,
            StoryWorkState.PAUSED_COST,
            StoryWorkState.RECOMPUTING,
            StoryWorkState.FAILED,
            StoryWorkState.DONE,
            StoryWorkState.CANCELLED,
        }
    ),
    StoryWorkState.PENDING_DECISION: frozenset(
        {
            StoryWorkState.RUNNING,
            StoryWorkState.RECOMPUTING,
            StoryWorkState.FAILED,
            StoryWorkState.CANCELLED,
        }
    ),
    StoryWorkState.PAUSED_QUOTA: frozenset(
        {StoryWorkState.RUNNING, StoryWorkState.CANCELLED, StoryWorkState.ARCHIVED}
    ),
    StoryWorkState.PAUSED_COST: frozenset(
        {StoryWorkState.RUNNING, StoryWorkState.CANCELLED, StoryWorkState.ARCHIVED}
    ),
    StoryWorkState.RECOMPUTING: frozenset(
        {
            StoryWorkState.RUNNING,
            StoryWorkState.PENDING_DECISION,
            StoryWorkState.FAILED,
            StoryWorkState.CANCELLED,
        }
    ),
    # 关键：FAILED 是可恢复终态，不得成为死路（PRD §3.2 / Plan M0 出口）。
    StoryWorkState.FAILED: frozenset(
        {
            StoryWorkState.RUNNING,
            StoryWorkState.RECOMPUTING,
            StoryWorkState.CANCELLED,
            StoryWorkState.ARCHIVED,
        }
    ),
    StoryWorkState.DONE: frozenset({StoryWorkState.RUNNING, StoryWorkState.ARCHIVED}),
    StoryWorkState.CANCELLED: frozenset({StoryWorkState.ARCHIVED}),
    StoryWorkState.ARCHIVED: frozenset(),
}

_CHAPTER_RUN_TRANSITIONS: dict[ChapterRunState, frozenset[ChapterRunState]] = {
    ChapterRunState.QUEUED: frozenset(
        {ChapterRunState.RUNNING, ChapterRunState.CANCELLED, ChapterRunState.SUPERSEDED}
    ),
    ChapterRunState.RUNNING: frozenset(
        {
            ChapterRunState.PENDING_DECISION,
            ChapterRunState.RETRYABLE_FAILED,
            ChapterRunState.TERMINAL_FAILED,
            ChapterRunState.CANONIZED,
            ChapterRunState.CANCELLED,
            ChapterRunState.SUPERSEDED,
        }
    ),
    ChapterRunState.PENDING_DECISION: frozenset(
        {
            ChapterRunState.RUNNING,
            ChapterRunState.RETRYABLE_FAILED,
            ChapterRunState.TERMINAL_FAILED,
            ChapterRunState.CANCELLED,
            ChapterRunState.SUPERSEDED,
        }
    ),
    ChapterRunState.RETRYABLE_FAILED: frozenset(
        {
            ChapterRunState.QUEUED,
            ChapterRunState.RUNNING,
            ChapterRunState.TERMINAL_FAILED,
            ChapterRunState.CANCELLED,
            ChapterRunState.SUPERSEDED,
        }
    ),
    ChapterRunState.TERMINAL_FAILED: frozenset(
        {ChapterRunState.QUEUED, ChapterRunState.CANCELLED, ChapterRunState.SUPERSEDED}
    ),
    ChapterRunState.CANONIZED: frozenset({ChapterRunState.SUPERSEDED}),
    ChapterRunState.SUPERSEDED: frozenset(),
    ChapterRunState.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    """非法状态迁移。"""

    def __init__(self, entity: str, current: str, target: str) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"invalid {entity} transition: {current} -> {target}")


def assert_story_work_transition(current: str, target: str) -> None:
    try:
        cur = StoryWorkState(current)
        tgt = StoryWorkState(target)
    except ValueError as exc:
        raise InvalidTransition("story_work", current, target) from exc
    if cur is tgt:
        return
    if tgt not in _STORY_WORK_TRANSITIONS[cur]:
        raise InvalidTransition("story_work", current, target)


def assert_chapter_run_transition(current: str, target: str) -> None:
    try:
        cur = ChapterRunState(current)
        tgt = ChapterRunState(target)
    except ValueError as exc:
        raise InvalidTransition("chapter_run", current, target) from exc
    if cur is tgt:
        return
    if tgt not in _CHAPTER_RUN_TRANSITIONS[cur]:
        raise InvalidTransition("chapter_run", current, target)


# 需要人工介入、且必须释放 worker 的状态（Tech-Spec §3.3）
WORKER_RELEASING_STATES: frozenset[StoryWorkState] = frozenset(
    {
        StoryWorkState.PENDING_DECISION,
        StoryWorkState.PAUSED_QUOTA,
        StoryWorkState.PAUSED_COST,
        StoryWorkState.FAILED,
        StoryWorkState.DONE,
        StoryWorkState.CANCELLED,
        StoryWorkState.ARCHIVED,
    }
)
