import uuid
from dataclasses import dataclass
from enum import StrEnum

from regent.application.p1_contracts import canonical_hash


class ResolutionMethod(StrEnum):
    REUSE = "REUSE"
    CONFIGURE = "CONFIGURE"
    COMPOSE = "COMPOSE"
    BUILD = "BUILD"
    REQUEST_HUMAN = "REQUEST_HUMAN"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    id: uuid.UUID
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class ToolCandidate:
    id: uuid.UUID
    capability_name: str
    status: str


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    requirement_key: str
    capability_name: str
    composable_from: tuple[str, ...] = ()
    build_allowed: bool = False
    human_resolvable: bool = False


@dataclass(frozen=True, slots=True)
class ResolutionItem:
    requirement_key: str
    capability_name: str
    gap_type: str
    method: ResolutionMethod
    capability_id: uuid.UUID | None = None
    tool_spec_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ResolutionPlan:
    items: tuple[ResolutionItem, ...]
    content_hash: str
    policy_version: str = "capability-resolution-v1"


class CapabilityResolutionService:
    def resolve(
        self,
        gaps: list[CapabilityGap],
        capabilities: list[CapabilityCandidate],
        tools: list[ToolCandidate],
    ) -> ResolutionPlan:
        reusable = {
            item.name: item
            for item in capabilities
            if item.status in {"VERIFIED", "GOAL_CERTIFIED"}
        }
        configurable = {item.capability_name: item for item in tools if item.status == "CERTIFIED"}
        items: list[ResolutionItem] = []
        for gap in gaps:
            capability = reusable.get(gap.capability_name)
            tool = configurable.get(gap.capability_name)
            if capability is not None:
                item = ResolutionItem(
                    gap.requirement_key,
                    gap.capability_name,
                    "NONE",
                    ResolutionMethod.REUSE,
                    capability_id=capability.id,
                )
            elif tool is not None:
                item = ResolutionItem(
                    gap.requirement_key,
                    gap.capability_name,
                    "CONFIGURATION",
                    ResolutionMethod.CONFIGURE,
                    tool_spec_id=tool.id,
                )
            elif gap.composable_from and all(name in reusable for name in gap.composable_from):
                item = ResolutionItem(
                    gap.requirement_key,
                    gap.capability_name,
                    "COMPOSITION",
                    ResolutionMethod.COMPOSE,
                )
            elif gap.build_allowed:
                item = ResolutionItem(
                    gap.requirement_key, gap.capability_name, "MISSING", ResolutionMethod.BUILD
                )
            elif gap.human_resolvable:
                item = ResolutionItem(
                    gap.requirement_key,
                    gap.capability_name,
                    "AUTHORITY",
                    ResolutionMethod.REQUEST_HUMAN,
                )
            else:
                item = ResolutionItem(
                    gap.requirement_key, gap.capability_name, "UNRESOLVED", ResolutionMethod.BLOCK
                )
            items.append(item)
        payload = [
            {
                "requirement_key": item.requirement_key,
                "capability_name": item.capability_name,
                "gap_type": item.gap_type,
                "method": item.method.value,
                "capability_id": str(item.capability_id) if item.capability_id else None,
                "tool_spec_id": str(item.tool_spec_id) if item.tool_spec_id else None,
            }
            for item in items
        ]
        return ResolutionPlan(tuple(items), canonical_hash(payload))
