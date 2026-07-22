from dataclasses import dataclass
from enum import StrEnum

from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import (
    GOAL_TERMINAL_STATES,
    RUN_TERMINAL_STATES,
    WORK_TERMINAL_STATES,
    GoalState,
    RunState,
    WorkState,
)


class GoalCommand(StrEnum):
    QUALIFY = "qualify"
    ACTIVATE = "activate"
    PAUSE = "pause"
    RESUME = "resume"
    WAIT_FOR_HUMAN = "wait_for_human"
    HUMAN_RESOLVED = "human_resolved"
    HUMAN_BLOCKED = "human_blocked"
    HUMAN_TIMEOUT_EXHAUSTED = "human_timeout_exhausted"
    REPLAN = "replan"
    ACHIEVE = "achieve"
    EXHAUST = "exhaust"
    FAIL = "fail"
    CANCEL = "cancel"


class WorkCommand(StrEnum):
    MAKE_READY = "make_ready"
    START = "start"
    REQUEST_EVALUATION = "request_evaluation"
    ACCEPT = "accept"
    REJECT = "reject"
    RETRY = "retry"
    WAIT_FOR_HUMAN = "wait_for_human"
    HUMAN_RESOLVED = "human_resolved"
    BLOCK = "block"
    UNBLOCK = "unblock"
    MARK_UNKNOWN = "mark_unknown"
    CANCEL = "cancel"


class RunCommand(StrEnum):
    REQUEST_PERMIT = "request_permit"
    QUEUE = "queue"
    CLAIM = "claim"
    MARK_EXECUTED = "mark_executed"
    MARK_FAILED = "mark_failed"
    MARK_UNKNOWN = "mark_unknown"
    DENY = "deny"
    EXPIRE = "expire"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class Transitioned[StateT: (GoalState, WorkState, RunState)]:
    state: StateT
    version: int


_GOAL_TRANSITIONS: dict[tuple[GoalState, GoalCommand], GoalState] = {
    (GoalState.DRAFT, GoalCommand.QUALIFY): GoalState.READY,
    (GoalState.READY, GoalCommand.ACTIVATE): GoalState.ACTIVE,
    (GoalState.ACTIVE, GoalCommand.PAUSE): GoalState.PAUSED,
    (GoalState.PAUSED, GoalCommand.RESUME): GoalState.ACTIVE,
    (GoalState.ACTIVE, GoalCommand.WAIT_FOR_HUMAN): GoalState.WAITING_HUMAN,
    (GoalState.WAITING_HUMAN, GoalCommand.HUMAN_RESOLVED): GoalState.ACTIVE,
    (GoalState.WAITING_HUMAN, GoalCommand.HUMAN_BLOCKED): GoalState.BLOCKED,
    (
        GoalState.WAITING_HUMAN,
        GoalCommand.HUMAN_TIMEOUT_EXHAUSTED,
    ): GoalState.EXHAUSTED,
    (GoalState.BLOCKED, GoalCommand.REPLAN): GoalState.ACTIVE,
    (GoalState.ACTIVE, GoalCommand.ACHIEVE): GoalState.ACHIEVED,
    (GoalState.ACTIVE, GoalCommand.EXHAUST): GoalState.EXHAUSTED,
    (GoalState.BLOCKED, GoalCommand.EXHAUST): GoalState.EXHAUSTED,
}
for goal_state in GoalState:
    if goal_state not in GOAL_TERMINAL_STATES:
        _GOAL_TRANSITIONS[(goal_state, GoalCommand.FAIL)] = GoalState.FAILED
        _GOAL_TRANSITIONS[(goal_state, GoalCommand.CANCEL)] = GoalState.CANCELLED

_WORK_TRANSITIONS: dict[tuple[WorkState, WorkCommand], WorkState] = {
    (WorkState.PLANNED, WorkCommand.MAKE_READY): WorkState.READY,
    (WorkState.READY, WorkCommand.START): WorkState.RUNNING,
    (WorkState.RUNNING, WorkCommand.REQUEST_EVALUATION): WorkState.EVALUATING,
    (WorkState.EVALUATING, WorkCommand.ACCEPT): WorkState.ACCEPTED,
    (WorkState.EVALUATING, WorkCommand.REJECT): WorkState.REJECTED,
    (WorkState.REJECTED, WorkCommand.RETRY): WorkState.READY,
    (WorkState.UNKNOWN, WorkCommand.RETRY): WorkState.READY,
    (WorkState.READY, WorkCommand.WAIT_FOR_HUMAN): WorkState.WAITING_HUMAN,
    (WorkState.WAITING_HUMAN, WorkCommand.HUMAN_RESOLVED): WorkState.READY,
    (WorkState.READY, WorkCommand.BLOCK): WorkState.BLOCKED,
    (WorkState.BLOCKED, WorkCommand.UNBLOCK): WorkState.READY,
    (WorkState.RUNNING, WorkCommand.MARK_UNKNOWN): WorkState.UNKNOWN,
}
for work_state in WorkState:
    if work_state not in WORK_TERMINAL_STATES:
        _WORK_TRANSITIONS[(work_state, WorkCommand.CANCEL)] = WorkState.CANCELLED

_RUN_TRANSITIONS: dict[tuple[RunState, RunCommand], RunState] = {
    (RunState.CREATED, RunCommand.REQUEST_PERMIT): RunState.PERMIT_PENDING,
    (RunState.PERMIT_PENDING, RunCommand.QUEUE): RunState.QUEUED,
    (RunState.PERMIT_PENDING, RunCommand.DENY): RunState.DENIED,
    (RunState.PERMIT_PENDING, RunCommand.EXPIRE): RunState.EXPIRED,
    (RunState.QUEUED, RunCommand.CLAIM): RunState.RUNNING,
    (RunState.RUNNING, RunCommand.MARK_EXECUTED): RunState.EXECUTED,
    (RunState.RUNNING, RunCommand.MARK_FAILED): RunState.FAILED,
    (RunState.RUNNING, RunCommand.MARK_UNKNOWN): RunState.UNKNOWN,
}
for run_state in RunState:
    if run_state not in RUN_TERMINAL_STATES:
        _RUN_TRANSITIONS[(run_state, RunCommand.CANCEL)] = RunState.CANCELLED


def _transition[
    StateT: (GoalState, WorkState, RunState),
    CommandT: StrEnum,
](
    *,
    state: StateT,
    command: CommandT,
    version: int,
    expected_version: int,
    transitions: dict[tuple[StateT, CommandT], StateT],
    terminal_states: frozenset[StateT],
    terminal_error: ErrorCode = ErrorCode.INVALID_STATE,
) -> Transitioned[StateT]:
    if version != expected_version:
        raise DomainError(
            ErrorCode.VERSION_CONFLICT,
            f"expected version {expected_version}, current version {version}",
        )
    if state in terminal_states:
        raise DomainError(terminal_error, f"{state} is terminal")
    target = transitions.get((state, command))
    if target is None:
        raise DomainError(ErrorCode.INVALID_STATE, f"{command} is not allowed from {state}")
    return Transitioned(state=target, version=version + 1)


def transition_goal(
    state: GoalState,
    command: GoalCommand,
    *,
    version: int,
    expected_version: int,
) -> Transitioned[GoalState]:
    return _transition(
        state=state,
        command=command,
        version=version,
        expected_version=expected_version,
        transitions=_GOAL_TRANSITIONS,
        terminal_states=GOAL_TERMINAL_STATES,
        terminal_error=ErrorCode.GOAL_TERMINAL,
    )


def transition_work(
    state: WorkState,
    command: WorkCommand,
    *,
    version: int,
    expected_version: int,
) -> Transitioned[WorkState]:
    return _transition(
        state=state,
        command=command,
        version=version,
        expected_version=expected_version,
        transitions=_WORK_TRANSITIONS,
        terminal_states=WORK_TERMINAL_STATES,
    )


def transition_run(
    state: RunState,
    command: RunCommand,
    *,
    version: int,
    expected_version: int,
) -> Transitioned[RunState]:
    return _transition(
        state=state,
        command=command,
        version=version,
        expected_version=expected_version,
        transitions=_RUN_TRANSITIONS,
        terminal_states=RUN_TERMINAL_STATES,
    )
