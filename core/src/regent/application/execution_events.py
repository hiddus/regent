"""P1 execution main chain event catalog.

Defines all P1 main chain event type constants, event envelope, and Outbox event factory.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from regent.infrastructure.models import OutboxEventModel

# ---------------------------------------------------------------------------
# P1 main chain event type constants
# ---------------------------------------------------------------------------

GOAL_EXECUTION_REQUESTED = "GoalExecutionRequested"
DISCOVERY_ROUND_REQUESTED = "DiscoveryRoundRequested"
DISCOVERY_COMPLETED = "DiscoveryCompleted"
REQUIREMENT_REQUESTED = "RequirementRequested"
REQUIREMENT_VALIDATED = "RequirementValidated"
CAPABILITY_RESOLUTION_REQUESTED = "CapabilityResolutionRequested"
CAPABILITY_RESOLUTION_SATISFIED = "CapabilityResolutionSatisfied"
GENERATION_RUN_REQUESTED = "GenerationRunRequested"
WORKSPACE_SNAPSHOT_READY = "WorkspaceSnapshotReady"
DEPENDENCY_RESOLUTION_REQUESTED = "DependencyResolutionRequested"
APP_BUILD_REQUESTED = "AppBuildRequested"
APP_BUILD_PASSED = "AppBuildPassed"
PREVIEW_DEPLOYMENT_REQUESTED = "PreviewDeploymentRequested"
PREVIEW_DEPLOYMENT_SUCCEEDED = "PreviewDeploymentSucceeded"

# All P1 main chain event types (in execution order)
P1_MAIN_CHAIN_EVENTS: tuple[str, ...] = (
    GOAL_EXECUTION_REQUESTED,
    DISCOVERY_ROUND_REQUESTED,
    DISCOVERY_COMPLETED,
    REQUIREMENT_REQUESTED,
    REQUIREMENT_VALIDATED,
    CAPABILITY_RESOLUTION_REQUESTED,
    CAPABILITY_RESOLUTION_SATISFIED,
    GENERATION_RUN_REQUESTED,
    WORKSPACE_SNAPSHOT_READY,
    DEPENDENCY_RESOLUTION_REQUESTED,
    APP_BUILD_REQUESTED,
    APP_BUILD_PASSED,
    PREVIEW_DEPLOYMENT_REQUESTED,
    PREVIEW_DEPLOYMENT_SUCCEEDED,
)

# ---------------------------------------------------------------------------
# Failure codes
# ---------------------------------------------------------------------------

FAILURE_GOAL_NOT_ACTIVE = "GOAL_NOT_ACTIVE"
FAILURE_SPEC_NOT_FROZEN = "SPEC_NOT_FROZEN"
FAILURE_PROJECT_NOT_ACTIVE = "PROJECT_NOT_ACTIVE"
FAILURE_DISCOVERY_CREATION_FAILED = "DISCOVERY_CREATION_FAILED"


# ---------------------------------------------------------------------------
# Event envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """P1 main chain event envelope."""

    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    correlation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    causation_id: uuid.UUID | None = None


def make_outbox_event(
    envelope: EventEnvelope,
    *,
    status: str = "PENDING",
) -> OutboxEventModel:
    """Create OutboxEventModel from EventEnvelope."""
    return OutboxEventModel(
        id=uuid.uuid4(),
        event_type=envelope.event_type,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        aggregate_version=envelope.aggregate_version,
        payload=envelope.payload,
        status=status,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
    )


def make_idempotency_key(prefix: str, goal_id: uuid.UUID, execution_event_id: str) -> str:
    """Generate idempotency key.

    Format: {prefix}:{goal_id}:{hash(execution_event_id)}
    The hash ensures the key stays within DB column limits (VARCHAR 255).
    """
    import hashlib

    tail_hash = hashlib.sha256(execution_event_id.encode()).hexdigest()[:16]
    key = f"{prefix}:{goal_id}:{tail_hash}"
    return key[:255]
