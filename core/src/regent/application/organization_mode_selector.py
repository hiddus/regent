"""Organization mode selector — match goal profile to execution strategy.

Given a ``GoalProfile`` (from ``goal_classifier.py``), this module recommends
the most appropriate execution mode. The orchestrator then uses this
recommendation to choose which pipeline stages to run.

Modes:
- ``waterfall``: Full Discovery → Requirement → Capability → Generation → Build → Preview.
  Best for: LARGE, HIGH complexity, well-defined requirements.
- ``agile``: Skip Discovery/Requirement, go straight to Generation, then iterate.
  Best for: SMALL/LOW, static-web, simple goals.
- ``hub_spoke``: Central coordinator dispatches to specialists, monitors, repairs.
  Best for: interactive-app, MEDIUM+ complexity, HEAVY iteration need.
- ``batch``: No Preview; validate via data output.
  Best for: data-pipeline, ETL, analytics.

The selector is deterministic and rule-based — no LLM calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from regent.application.goal_classifier import GoalProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OrganizationMode:
    """A recommended execution strategy."""

    mode_id: str                    # waterfall / agile / hub_spoke / batch
    label: str                      # Human-readable label
    skip_discovery: bool            # Skip Discovery stage?
    skip_requirement: bool          # Skip Requirement stage?
    skip_capability: bool           # Skip Capability Resolution?
    enable_monitoring: bool         # Enable runtime behavior monitoring?
    enable_repair_loop: bool        # Enable behavior repair cycle?
    max_iterations: int             # Max generation→monitor→fix cycles
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode_id": self.mode_id,
            "label": self.label,
            "skip_discovery": self.skip_discovery,
            "skip_requirement": self.skip_requirement,
            "skip_capability": self.skip_capability,
            "enable_monitoring": self.enable_monitoring,
            "enable_repair_loop": self.enable_repair_loop,
            "max_iterations": self.max_iterations,
            "rationale": self.rationale,
        }


# Pre-defined modes
WATERFALL = OrganizationMode(
    mode_id="waterfall",
    label="瀑布式（完整管线）",
    skip_discovery=False,
    skip_requirement=False,
    skip_capability=False,
    enable_monitoring=False,
    enable_repair_loop=False,
    max_iterations=1,
    rationale="需求明确、复杂度高，适合完整管线确保质量。",
)

AGILE = OrganizationMode(
    mode_id="agile",
    label="敏捷快速模式（跳过分析，直接生成）",
    skip_discovery=True,
    skip_requirement=True,
    skip_capability=True,
    enable_monitoring=False,
    enable_repair_loop=False,
    max_iterations=3,
    rationale="简单目标无需过度抽象，快速迭代更高效。",
)

HUB_SPOKE = OrganizationMode(
    mode_id="hub_spoke",
    label="中心辐射式（协调→执行→监控→修复）",
    skip_discovery=True,
    skip_requirement=False,
    skip_capability=True,
    enable_monitoring=True,
    enable_repair_loop=True,
    max_iterations=10,
    rationale="交互型应用需要持续观察行为质量并自动修复。",
)

BATCH = OrganizationMode(
    mode_id="batch",
    label="批处理模式（无 Preview，数据验证）",
    skip_discovery=False,
    skip_requirement=False,
    skip_capability=False,
    enable_monitoring=False,
    enable_repair_loop=False,
    max_iterations=2,
    rationale="数据处理类任务用输出数据验证，不需要 Preview。",
)


def select_mode(profile: GoalProfile) -> OrganizationMode:
    """Select the best organization mode for a goal profile.

    Rules (evaluated in priority order):
    1. data-pipeline domain → batch
    2. interactive-app + (MEDIUM+ complexity or HEAVY iteration) → hub_spoke
    3. SMALL + LOW complexity → agile
    4. LARGE + HIGH complexity → waterfall
    5. Default → agile for SMALL, waterfall for LARGE, hub_spoke otherwise
    """
    # Rule 1: data-pipeline → batch
    if profile.domain == "data-pipeline":
        return BATCH

    # Rule 2: interactive-app with complexity/iteration → hub_spoke
    if profile.domain == "interactive-app":
        if profile.complexity in {"MEDIUM", "HIGH"} or profile.iteration_need in {"HEAVY"}:
            return HUB_SPOKE
        if profile.monitoring_need == "CONTINUOUS":
            return HUB_SPOKE
        # Simple interactive app still benefits from monitoring
        return HUB_SPOKE

    # Rule 3: simple goals → agile
    if profile.scale == "SMALL" and profile.complexity == "LOW":
        return AGILE

    # Rule 4: complex, well-defined → waterfall
    if profile.scale == "LARGE" and profile.complexity == "HIGH":
        return WATERFALL

    # Rule 5: defaults by scale
    if profile.scale == "SMALL":
        return AGILE
    if profile.scale == "LARGE":
        return WATERFALL

    # MEDIUM scale with iteration need → hub_spoke
    if profile.iteration_need in {"LIGHT", "HEAVY"}:
        return HUB_SPOKE

    # Default: agile for unknown
    return AGILE


def select_mode_from_metadata(
    goal_input: str = "",
    *,
    metadata: dict[str, Any] | None = None,
    goal_scale: str | None = None,
    spec_constraints: dict[str, Any] | None = None,
    spec_success_criteria: dict[str, Any] | None = None,
) -> tuple[OrganizationMode, GoalProfile]:
    """Convenience: classify + select in one call.

    Returns (mode, profile) so callers can store both in metadata.
    """
    from regent.application.goal_classifier import GoalClassifier

    classifier = GoalClassifier()
    profile = classifier.classify(
        goal_input,
        spec_constraints=spec_constraints,
        spec_success_criteria=spec_success_criteria,
        metadata=metadata,
        goal_scale=goal_scale,
    )
    mode = select_mode(profile)
    return mode, profile
