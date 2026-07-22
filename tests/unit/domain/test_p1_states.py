import pytest
from regent.domain.errors import DomainError
from regent.domain.p1_states import (
    BuildCommand,
    BuildState,
    DiscoveryCommand,
    DiscoveryState,
    GenerationRunCommand,
    GenerationRunState,
    transition_build,
    transition_discovery,
    transition_generation_run,
)


def test_discovery_happy_path_increments_version() -> None:
    result = transition_discovery(
        DiscoveryState.REQUESTED, DiscoveryCommand.START_RESEARCH, version=2, expected_version=2
    )
    assert result.state is DiscoveryState.RESEARCHING
    assert result.version == 3


def test_generation_rejects_skipped_phase() -> None:
    with pytest.raises(DomainError):
        transition_generation_run(
            GenerationRunState.REQUESTED,
            GenerationRunCommand.COMPLETE,
            version=0,
            expected_version=0,
        )


def test_terminal_build_cannot_transition() -> None:
    with pytest.raises(DomainError):
        transition_build(BuildState.PASSED, BuildCommand.START, version=1, expected_version=1)


def test_version_conflict_is_rejected() -> None:
    with pytest.raises(DomainError):
        transition_discovery(
            DiscoveryState.REQUESTED, DiscoveryCommand.START_RESEARCH, version=3, expected_version=2
        )
