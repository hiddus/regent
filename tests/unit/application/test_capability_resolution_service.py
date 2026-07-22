import uuid

from regent.application.capability_resolution_service import (
    CapabilityCandidate,
    CapabilityGap,
    CapabilityResolutionService,
    ResolutionMethod,
    ToolCandidate,
)


def test_resolution_uses_frozen_priority_order() -> None:
    reusable = CapabilityCandidate(uuid.uuid4(), "existing", "VERIFIED")
    component = CapabilityCandidate(uuid.uuid4(), "component", "GOAL_CERTIFIED")
    tool = ToolCandidate(uuid.uuid4(), "configurable", "CERTIFIED")
    gaps = [
        CapabilityGap("r1", "existing", build_allowed=True),
        CapabilityGap("r2", "configurable", build_allowed=True),
        CapabilityGap("r3", "composed", composable_from=("component",), build_allowed=True),
        CapabilityGap("r4", "buildable", build_allowed=True),
        CapabilityGap("r5", "human", human_resolvable=True),
        CapabilityGap("r6", "blocked"),
    ]
    plan = CapabilityResolutionService().resolve(gaps, [reusable, component], [tool])
    assert [item.method for item in plan.items] == [
        ResolutionMethod.REUSE,
        ResolutionMethod.CONFIGURE,
        ResolutionMethod.COMPOSE,
        ResolutionMethod.BUILD,
        ResolutionMethod.REQUEST_HUMAN,
        ResolutionMethod.BLOCK,
    ]


def test_resolution_hash_is_deterministic() -> None:
    gaps = [CapabilityGap("requirement", "missing", build_allowed=True)]
    service = CapabilityResolutionService()
    assert service.resolve(gaps, [], []).content_hash == service.resolve(gaps, [], []).content_hash
