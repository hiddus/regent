"""Outbox event product scope for claim filtering.

Novel chapter advancement currently does not claim Outbox events; novel workers
subscribe to an empty set so they never steal legacy work. TimerFired is treated
as legacy-owned until producers stamp an unambiguous product_scope.
"""

from __future__ import annotations

from typing import Iterable

from regent.application import execution_events as ee
from regent.bootstrap.service_mode import ServiceMode

# Observability / ack-only handlers currently registered on the legacy worker.
_LEGACY_OBSERVABILITY: frozenset[str] = frozenset(
    {
        "GoalStateChanged",
        "GoalSpecFrozen",
        "WorkStateChanged",
        "RunStateChanged",
        "DeliveryStateChanged",
        ee.DELIVERY_STATE_CHANGED,
        ee.DELIVERY_GAP_HUMAN_APPROVED,
        "TimerFired",
    }
)

LEGACY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        *ee.P1_MAIN_CHAIN_EVENTS,
        ee.DELIVERY_GAP_HUMAN_APPROVED,
        ee.DELIVERY_STATE_CHANGED,
        *_LEGACY_OBSERVABILITY,
        # Delivery batch variants may appear in older outbox rows.
        ee.DELIVERY_BATCH_PLANNED,
        ee.DELIVERY_BATCH_STARTED,
        ee.DELIVERY_BATCH_VERIFIED,
        ee.DELIVERY_BATCH_MERGED,
        ee.DELIVERY_BATCH_REJECTED,
        ee.DELIVERY_BATCHES_COMPLETED,
        ee.DELIVERY_GLOBAL_VERIFY_FAILED,
        ee.APP_BUILD_PASSED,
        ee.REQUIREMENT_VALIDATED,
    }
)

# Novel product path uses chapter-run polling + novel recovery, not Outbox claim.
NOVEL_EVENT_TYPES: frozenset[str] = frozenset()

# Reserved for future shared events that both products may claim after scope stamps.
SHARED_EVENT_TYPES: frozenset[str] = frozenset()


def classify_event_type(event_type: str) -> str:
    """Return novel | legacy | shared | unknown."""
    name = str(event_type or "")
    if name in NOVEL_EVENT_TYPES:
        return "novel"
    if name in SHARED_EVENT_TYPES:
        return "shared"
    if name in LEGACY_EVENT_TYPES:
        return "legacy"
    return "unknown"


def claimable_event_types(mode: ServiceMode) -> frozenset[str] | None:
    """Types a worker in ``mode`` may claim.

    ``None`` means no SQL filter (combined compatibility).
    Empty frozenset means claim nothing.
    """
    if mode is ServiceMode.COMBINED:
        return None
    if mode is ServiceMode.NOVEL:
        return NOVEL_EVENT_TYPES | SHARED_EVENT_TYPES
    if mode is ServiceMode.LEGACY:
        return LEGACY_EVENT_TYPES | SHARED_EVENT_TYPES
    raise ValueError(f"unsupported service mode: {mode!r}")


def is_known_event_type(event_type: str) -> bool:
    return classify_event_type(event_type) != "unknown"


def describe_scopes() -> dict[str, Iterable[str]]:
    return {
        "novel": sorted(NOVEL_EVENT_TYPES),
        "legacy": sorted(LEGACY_EVENT_TYPES),
        "shared": sorted(SHARED_EVENT_TYPES),
    }
