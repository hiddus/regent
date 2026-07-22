import pytest
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import GoalState, RunState, WorkState
from regent.domain.transitions import (
    GoalCommand,
    RunCommand,
    WorkCommand,
    transition_goal,
    transition_run,
    transition_work,
)


@pytest.mark.parametrize(
    ("state", "command", "target"),
    [
        (GoalState.DRAFT, GoalCommand.QUALIFY, GoalState.READY),
        (GoalState.READY, GoalCommand.ACTIVATE, GoalState.ACTIVE),
        (GoalState.ACTIVE, GoalCommand.PAUSE, GoalState.PAUSED),
        (GoalState.PAUSED, GoalCommand.RESUME, GoalState.ACTIVE),
        (
            GoalState.ACTIVE,
            GoalCommand.WAIT_FOR_HUMAN,
            GoalState.WAITING_HUMAN,
        ),
        (
            GoalState.WAITING_HUMAN,
            GoalCommand.HUMAN_BLOCKED,
            GoalState.BLOCKED,
        ),
        (GoalState.BLOCKED, GoalCommand.REPLAN, GoalState.ACTIVE),
        (GoalState.ACTIVE, GoalCommand.ACHIEVE, GoalState.ACHIEVED),
    ],
)
def test_goal_legal_transitions(state: GoalState, command: GoalCommand, target: GoalState) -> None:
    result = transition_goal(state, command, version=4, expected_version=4)
    assert result.state == target
    assert result.version == 5


@pytest.mark.parametrize(
    ("state", "command", "target"),
    [
        (WorkState.PLANNED, WorkCommand.MAKE_READY, WorkState.READY),
        (WorkState.READY, WorkCommand.START, WorkState.RUNNING),
        (
            WorkState.RUNNING,
            WorkCommand.REQUEST_EVALUATION,
            WorkState.EVALUATING,
        ),
        (WorkState.EVALUATING, WorkCommand.ACCEPT, WorkState.ACCEPTED),
        (WorkState.EVALUATING, WorkCommand.REJECT, WorkState.REJECTED),
        (WorkState.REJECTED, WorkCommand.RETRY, WorkState.READY),
        (WorkState.UNKNOWN, WorkCommand.RETRY, WorkState.READY),
        (WorkState.RUNNING, WorkCommand.MARK_UNKNOWN, WorkState.UNKNOWN),
    ],
)
def test_work_legal_transitions(state: WorkState, command: WorkCommand, target: WorkState) -> None:
    result = transition_work(state, command, version=0, expected_version=0)
    assert result.state == target
    assert result.version == 1


@pytest.mark.parametrize(
    ("state", "command", "target"),
    [
        (RunState.CREATED, RunCommand.REQUEST_PERMIT, RunState.PERMIT_PENDING),
        (RunState.PERMIT_PENDING, RunCommand.QUEUE, RunState.QUEUED),
        (RunState.QUEUED, RunCommand.CLAIM, RunState.RUNNING),
        (RunState.RUNNING, RunCommand.MARK_EXECUTED, RunState.EXECUTED),
        (RunState.PERMIT_PENDING, RunCommand.DENY, RunState.DENIED),
        (RunState.PERMIT_PENDING, RunCommand.EXPIRE, RunState.EXPIRED),
    ],
)
def test_run_legal_transitions(state: RunState, command: RunCommand, target: RunState) -> None:
    result = transition_run(state, command, version=2, expected_version=2)
    assert result.state == target
    assert result.version == 3


def test_illegal_transition_has_stable_error() -> None:
    with pytest.raises(DomainError) as raised:
        transition_goal(
            GoalState.DRAFT,
            GoalCommand.ACHIEVE,
            version=0,
            expected_version=0,
        )
    assert raised.value.code == ErrorCode.INVALID_STATE


def test_goal_terminal_cannot_reopen() -> None:
    with pytest.raises(DomainError) as raised:
        transition_goal(
            GoalState.ACHIEVED,
            GoalCommand.ACTIVATE,
            version=7,
            expected_version=7,
        )
    assert raised.value.code == ErrorCode.GOAL_TERMINAL


def test_work_terminal_cannot_retry() -> None:
    with pytest.raises(DomainError) as raised:
        transition_work(
            WorkState.ACCEPTED,
            WorkCommand.RETRY,
            version=3,
            expected_version=3,
        )
    assert raised.value.code == ErrorCode.INVALID_STATE


def test_run_terminal_is_immutable() -> None:
    with pytest.raises(DomainError) as raised:
        transition_run(
            RunState.UNKNOWN,
            RunCommand.MARK_EXECUTED,
            version=5,
            expected_version=5,
        )
    assert raised.value.code == ErrorCode.INVALID_STATE


def test_version_conflict_is_checked_before_transition() -> None:
    with pytest.raises(DomainError) as raised:
        transition_goal(
            GoalState.ACTIVE,
            GoalCommand.PAUSE,
            version=6,
            expected_version=5,
        )
    assert raised.value.code == ErrorCode.VERSION_CONFLICT
