from enum import StrEnum


class AggregateType(StrEnum):
    GOAL = "goal"
    WORK = "work"
    RUN = "run"


class GoalState(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    WAITING_HUMAN = "WAITING_HUMAN"
    BLOCKED = "BLOCKED"
    ACHIEVED = "ACHIEVED"
    EXHAUSTED = "EXHAUSTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkState(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    RUNNING = "RUNNING"
    EVALUATING = "EVALUATING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WAITING_HUMAN = "WAITING_HUMAN"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class RunState(StrEnum):
    CREATED = "CREATED"
    PERMIT_PENDING = "PERMIT_PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


GOAL_TERMINAL_STATES = frozenset(
    {GoalState.ACHIEVED, GoalState.EXHAUSTED, GoalState.FAILED, GoalState.CANCELLED}
)
WORK_TERMINAL_STATES = frozenset({WorkState.ACCEPTED, WorkState.CANCELLED})
RUN_TERMINAL_STATES = frozenset(
    {
        RunState.EXECUTED,
        RunState.FAILED,
        RunState.UNKNOWN,
        RunState.DENIED,
        RunState.EXPIRED,
        RunState.CANCELLED,
    }
)
