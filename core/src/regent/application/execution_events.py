"""P1 execution main chain event catalog + V3 domain events.

Defines all event type constants (P1 main chain + V3 domain events),
event envelope, and Outbox event factory.
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
DELIVERY_BATCH_PLANNED = "DeliveryBatchPlanned"
DELIVERY_BATCH_STARTED = "DeliveryBatchStarted"
DELIVERY_BATCH_VERIFIED = "DeliveryBatchVerified"
DELIVERY_BATCH_MERGED = "DeliveryBatchMerged"
DELIVERY_BATCH_REJECTED = "DeliveryBatchRejected"
DELIVERY_BATCHES_COMPLETED = "DeliveryBatchesCompleted"
DELIVERY_GLOBAL_VERIFY_FAILED = "DeliveryGlobalVerifyFailed"
DEPENDENCY_RESOLUTION_REQUESTED = "DependencyResolutionRequested"
APP_BUILD_REQUESTED = "AppBuildRequested"
APP_BUILD_PASSED = "AppBuildPassed"
PREVIEW_DEPLOYMENT_REQUESTED = "PreviewDeploymentRequested"
PREVIEW_DEPLOYMENT_SUCCEEDED = "PreviewDeploymentSucceeded"
QUALITY_APPROVAL_REQUESTED = "QualityApprovalRequested"
QUALITY_APPROVAL_COMPLETED = "QualityApprovalCompleted"
RELEASE_APPROVAL_COMPLETED = "ReleaseApprovalCompleted"
DELIVERY_GAP_HUMAN_APPROVED = "DeliveryGapHumanApproved"
DELIVERY_STATE_CHANGED = "DeliveryStateChanged"

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
    QUALITY_APPROVAL_REQUESTED,
    QUALITY_APPROVAL_COMPLETED,
    RELEASE_APPROVAL_COMPLETED,
)

# ---------------------------------------------------------------------------
# Failure codes
# ---------------------------------------------------------------------------

FAILURE_GOAL_NOT_ACTIVE = "GOAL_NOT_ACTIVE"
FAILURE_SPEC_NOT_FROZEN = "SPEC_NOT_FROZEN"
FAILURE_PROJECT_NOT_ACTIVE = "PROJECT_NOT_ACTIVE"
FAILURE_DISCOVERY_CREATION_FAILED = "DISCOVERY_CREATION_FAILED"
FAILURE_COMPLIANCE = "FAILURE_COMPLIANCE"

# ---------------------------------------------------------------------------
# V3 Domain Events (supplementing P1 main chain)
# These events cover the full V3 §4 event-driven iteration loop.
# ---------------------------------------------------------------------------

# Goal lifecycle events
GOAL_DRAFTED = "GoalDrafted"
GOAL_FROZEN = "GoalFrozen"
GOAL_STARTED = "GoalStarted"
GOAL_ACHIEVED = "GoalAchieved"
GOAL_EXHAUSTED = "GoalExhausted"
GOAL_FAILED = "GoalFailed"
GOAL_CANCELLED = "GoalCancelled"
GOAL_PAUSED = "GoalPaused"
GOAL_BLOCKED = "GoalBlocked"

# Work events
WORK_CREATED = "WorkCreated"
WORK_ACCEPTED = "WorkAccepted"
WORK_REJECTED = "WorkRejected"

# Run events
RUN_DISPATCHED = "RunDispatched"
RUN_EXECUTED = "RunExecuted"
RUN_FAILED = "RunFailed"
RUN_UNKNOWN = "RunUnknown"

# Permit events
PERMIT_REQUESTED = "PermitRequested"
PERMIT_GRANTED = "PermitGranted"
PERMIT_CLAIMED = "PermitClaimed"
PERMIT_CONSUMED = "PermitConsumed"
PERMIT_REVOKED = "PermitRevoked"

# HumanTask events
HUMANTASK_CREATED = "HumanTaskCreated"
HUMANTASK_COMPLETED = "HumanTaskCompleted"
HUMANTASK_ESCALATED = "HumanTaskEscalated"

# Evidence & Observation events
EVIDENCE_ARRIVED = "EvidenceArrived"
OBSERVATION_SIGNED = "ObservationSigned"
GATE_EVALUATED = "GateEvaluated"

# Organization events
REORGANIZATION_TRIGGERED = "ReorganizationTriggered"
ORGANIZATION_SELECTED = "OrganizationSelected"

# Constraint & Governance events
CONSTRAINT_VIOLATED = "ConstraintViolated"
COMPLIANCE_CHECK_PASSED = "ComplianceCheckPassed"
COMPLIANCE_CHECK_FAILED = "ComplianceCheckFailed"
RISK_ESCALATED = "RiskEscalated"

# System events
RECONCILING_REQUIRED = "ReconcilingRequired"
BUCKET_EXCEEDED = "BucketExceeded"

# All V3 domain event types
V3_DOMAIN_EVENTS: tuple[str, ...] = (
    GOAL_DRAFTED, GOAL_FROZEN, GOAL_STARTED, GOAL_ACHIEVED,
    GOAL_EXHAUSTED, GOAL_FAILED, GOAL_CANCELLED, GOAL_PAUSED, GOAL_BLOCKED,
    WORK_CREATED, WORK_ACCEPTED, WORK_REJECTED,
    RUN_DISPATCHED, RUN_EXECUTED, RUN_FAILED, RUN_UNKNOWN,
    PERMIT_REQUESTED, PERMIT_GRANTED, PERMIT_CLAIMED, PERMIT_CONSUMED, PERMIT_REVOKED,
    HUMANTASK_CREATED, HUMANTASK_COMPLETED, HUMANTASK_ESCALATED,
    EVIDENCE_ARRIVED, OBSERVATION_SIGNED, GATE_EVALUATED,
    REORGANIZATION_TRIGGERED, ORGANIZATION_SELECTED,
    CONSTRAINT_VIOLATED, COMPLIANCE_CHECK_PASSED, COMPLIANCE_CHECK_FAILED, RISK_ESCALATED,
    RECONCILING_REQUIRED, BUCKET_EXCEEDED,
)


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
