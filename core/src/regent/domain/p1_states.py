from dataclasses import dataclass
from enum import StrEnum

from regent.domain.errors import DomainError, ErrorCode


class DiscoveryState(StrEnum):
    REQUESTED = "REQUESTED"
    RESEARCHING = "RESEARCHING"
    READY = "READY"
    DECIDED = "DECIDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    EXHAUSTED = "EXHAUSTED"


class DiscoveryCommand(StrEnum):
    START_RESEARCH = "start_research"
    MARK_READY = "mark_ready"
    DECIDE = "decide"
    BLOCK = "block"
    RESUME = "resume"
    FAIL = "fail"
    EXHAUST = "exhaust"


class RequirementRevisionState(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class RequirementRevisionCommand(StrEnum):
    VALIDATE = "validate"
    SUPERSEDE = "supersede"
    WITHDRAW = "withdraw"


class GenerationRunState(StrEnum):
    REQUESTED = "REQUESTED"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    COMMITTING = "COMMITTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class GenerationRunCommand(StrEnum):
    START_PLANNING = "start_planning"
    START_GENERATING = "start_generating"
    START_VALIDATING = "start_validating"
    START_COMMITTING = "start_committing"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


class BuildState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class BuildCommand(StrEnum):
    START = "start"
    PASS = "pass"
    FAIL = "fail"
    MARK_UNKNOWN = "mark_unknown"
    RECONCILE_PASSED = "reconcile_passed"
    RECONCILE_FAILED = "reconcile_failed"


class DeploymentState(StrEnum):
    REQUESTED = "REQUESTED"
    DEPLOYING = "DEPLOYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"


class DeploymentCommand(StrEnum):
    START = "start"
    SUCCEED = "succeed"
    FAIL = "fail"
    MARK_UNKNOWN = "mark_unknown"
    RECONCILE_SUCCEEDED = "reconcile_succeeded"
    RECONCILE_FAILED = "reconcile_failed"
    SUPERSEDE = "supersede"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class P1TransitionResult:
    state: StrEnum
    version: int


def _transition[StateT: StrEnum, CommandT: StrEnum](
    state: StateT,
    command: CommandT,
    *,
    version: int,
    expected_version: int,
    transitions: dict[tuple[StateT, CommandT], StateT],
    terminal: frozenset[StateT],
) -> P1TransitionResult:
    if version != expected_version:
        raise DomainError(
            ErrorCode.VERSION_CONFLICT,
            f"expected version {expected_version}, current version {version}",
        )
    if state in terminal:
        raise DomainError(ErrorCode.INVALID_STATE, f"{state} is terminal")
    target = transitions.get((state, command))
    if target is None:
        raise DomainError(ErrorCode.INVALID_STATE, f"{command} is not allowed from {state}")
    return P1TransitionResult(target, version + 1)


_DISCOVERY = {
    (DiscoveryState.REQUESTED, DiscoveryCommand.START_RESEARCH): DiscoveryState.RESEARCHING,
    (DiscoveryState.RESEARCHING, DiscoveryCommand.MARK_READY): DiscoveryState.READY,
    (DiscoveryState.READY, DiscoveryCommand.DECIDE): DiscoveryState.DECIDED,
    (DiscoveryState.REQUESTED, DiscoveryCommand.BLOCK): DiscoveryState.BLOCKED,
    (DiscoveryState.RESEARCHING, DiscoveryCommand.BLOCK): DiscoveryState.BLOCKED,
    (DiscoveryState.BLOCKED, DiscoveryCommand.RESUME): DiscoveryState.RESEARCHING,
    (DiscoveryState.RESEARCHING, DiscoveryCommand.FAIL): DiscoveryState.FAILED,
    (DiscoveryState.READY, DiscoveryCommand.EXHAUST): DiscoveryState.EXHAUSTED,
}
_DISCOVERY_TERMINAL = frozenset(
    {DiscoveryState.DECIDED, DiscoveryState.FAILED, DiscoveryState.EXHAUSTED}
)


def transition_discovery(
    state: DiscoveryState,
    command: DiscoveryCommand,
    *,
    version: int,
    expected_version: int,
) -> P1TransitionResult:
    return _transition(
        state,
        command,
        version=version,
        expected_version=expected_version,
        transitions=_DISCOVERY,
        terminal=_DISCOVERY_TERMINAL,
    )


_REQUIREMENT = {
    (
        RequirementRevisionState.DRAFT,
        RequirementRevisionCommand.VALIDATE,
    ): RequirementRevisionState.VALIDATED,
    (
        RequirementRevisionState.VALIDATED,
        RequirementRevisionCommand.SUPERSEDE,
    ): RequirementRevisionState.SUPERSEDED,
    (
        RequirementRevisionState.DRAFT,
        RequirementRevisionCommand.WITHDRAW,
    ): RequirementRevisionState.WITHDRAWN,
    (
        RequirementRevisionState.VALIDATED,
        RequirementRevisionCommand.WITHDRAW,
    ): RequirementRevisionState.WITHDRAWN,
}
_REQUIREMENT_TERMINAL = frozenset(
    {RequirementRevisionState.SUPERSEDED, RequirementRevisionState.WITHDRAWN}
)


def transition_requirement_revision(
    state: RequirementRevisionState,
    command: RequirementRevisionCommand,
    *,
    version: int,
    expected_version: int,
) -> P1TransitionResult:
    return _transition(
        state,
        command,
        version=version,
        expected_version=expected_version,
        transitions=_REQUIREMENT,
        terminal=_REQUIREMENT_TERMINAL,
    )


_GENERATION = {
    (
        GenerationRunState.REQUESTED,
        GenerationRunCommand.START_PLANNING,
    ): GenerationRunState.PLANNING,
    (
        GenerationRunState.PLANNING,
        GenerationRunCommand.START_GENERATING,
    ): GenerationRunState.GENERATING,
    (
        GenerationRunState.GENERATING,
        GenerationRunCommand.START_VALIDATING,
    ): GenerationRunState.VALIDATING,
    (
        GenerationRunState.VALIDATING,
        GenerationRunCommand.START_COMMITTING,
    ): GenerationRunState.COMMITTING,
    (
        GenerationRunState.COMMITTING,
        GenerationRunCommand.COMPLETE,
    ): GenerationRunState.COMPLETED,
}
for generation_state in GenerationRunState:
    if generation_state not in {
        GenerationRunState.COMPLETED,
        GenerationRunState.FAILED,
        GenerationRunState.CANCELLED,
    }:
        _GENERATION[(generation_state, GenerationRunCommand.FAIL)] = GenerationRunState.FAILED
        _GENERATION[(generation_state, GenerationRunCommand.CANCEL)] = GenerationRunState.CANCELLED
_GENERATION_TERMINAL = frozenset(
    {
        GenerationRunState.COMPLETED,
        GenerationRunState.FAILED,
        GenerationRunState.CANCELLED,
    }
)


def transition_generation_run(
    state: GenerationRunState,
    command: GenerationRunCommand,
    *,
    version: int,
    expected_version: int,
) -> P1TransitionResult:
    return _transition(
        state,
        command,
        version=version,
        expected_version=expected_version,
        transitions=_GENERATION,
        terminal=_GENERATION_TERMINAL,
    )


_BUILD = {
    (BuildState.QUEUED, BuildCommand.START): BuildState.RUNNING,
    (BuildState.RUNNING, BuildCommand.PASS): BuildState.PASSED,
    (BuildState.RUNNING, BuildCommand.FAIL): BuildState.FAILED,
    (BuildState.RUNNING, BuildCommand.MARK_UNKNOWN): BuildState.UNKNOWN,
    (BuildState.UNKNOWN, BuildCommand.RECONCILE_PASSED): BuildState.PASSED,
    (BuildState.UNKNOWN, BuildCommand.RECONCILE_FAILED): BuildState.FAILED,
}
_BUILD_TERMINAL = frozenset({BuildState.PASSED, BuildState.FAILED})


def transition_build(
    state: BuildState,
    command: BuildCommand,
    *,
    version: int,
    expected_version: int,
) -> P1TransitionResult:
    return _transition(
        state,
        command,
        version=version,
        expected_version=expected_version,
        transitions=_BUILD,
        terminal=_BUILD_TERMINAL,
    )


_DEPLOYMENT = {
    (DeploymentState.REQUESTED, DeploymentCommand.START): DeploymentState.DEPLOYING,
    (DeploymentState.DEPLOYING, DeploymentCommand.SUCCEED): DeploymentState.SUCCEEDED,
    (DeploymentState.DEPLOYING, DeploymentCommand.FAIL): DeploymentState.FAILED,
    (DeploymentState.DEPLOYING, DeploymentCommand.MARK_UNKNOWN): DeploymentState.UNKNOWN,
    (
        DeploymentState.UNKNOWN,
        DeploymentCommand.RECONCILE_SUCCEEDED,
    ): DeploymentState.SUCCEEDED,
    (DeploymentState.UNKNOWN, DeploymentCommand.RECONCILE_FAILED): DeploymentState.FAILED,
    (DeploymentState.SUCCEEDED, DeploymentCommand.SUPERSEDE): DeploymentState.SUPERSEDED,
    (DeploymentState.SUCCEEDED, DeploymentCommand.ROLLBACK): DeploymentState.ROLLED_BACK,
}
_DEPLOYMENT_TERMINAL = frozenset(
    {
        DeploymentState.FAILED,
        DeploymentState.SUPERSEDED,
        DeploymentState.ROLLED_BACK,
    }
)


def transition_deployment(
    state: DeploymentState,
    command: DeploymentCommand,
    *,
    version: int,
    expected_version: int,
) -> P1TransitionResult:
    return _transition(
        state,
        command,
        version=version,
        expected_version=expected_version,
        transitions=_DEPLOYMENT,
        terminal=_DEPLOYMENT_TERMINAL,
    )
