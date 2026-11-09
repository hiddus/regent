"""First-class delivery role Agents: Product / Tech / Test / UX / Ops.

The certified Durable Hive (``pm-dev-independent-qa-v1``) only materializes
PM → Dev → structure QA. That is **not** enough for Regent's core delivery
goal: a complete product with verifiable detail, not an outline shell.

This module defines the delivery Agents that must follow app-project Goals.
Regent **self-supplements** the roster from Goal signals via
``select_roles_for_goal``. Roles are materialized as durable Deployments
(``delivery-roles-v1`` companion template) and execute ``delivery.*.review``
AgentTasks — independent of (and stricter than) structure Hive QA.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DELIVERY_ROLES_TEMPLATE_ID = "delivery-roles-v1"
DELIVERY_ROLE_CATALOG_SCHEMA = "regent-delivery-role-agents/v2"

# Default roster for app / preview Goals — Regent supplements these itself.
APP_PROJECT_ROLES: tuple[str, ...] = (
    "product",
    "tech",
    "test",
    "ux",
    "ops",
)


@dataclass(frozen=True, slots=True)
class DeliveryRoleAgent:
    """Canonical delivery Agent definition (not a skill pack injection)."""

    role_id: str
    task_type: str
    label_zh: str
    label_en: str
    harness_role: str
    skill_id: str
    responsibilities: tuple[str, ...]
    non_responsibilities: tuple[str, ...]
    acceptance_artifacts: tuple[str, ...]
    gap_codes: tuple[str, ...]
    independent_reviewer: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "task_type": self.task_type,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "harness_role": self.harness_role,
            "skill_id": self.skill_id,
            "responsibilities": list(self.responsibilities),
            "non_responsibilities": list(self.non_responsibilities),
            "acceptance_artifacts": list(self.acceptance_artifacts),
            "gap_codes": list(self.gap_codes),
            "independent_reviewer": self.independent_reviewer,
        }


PRODUCT_AGENT = DeliveryRoleAgent(
    role_id="product",
    task_type="delivery.product.review",
    label_zh="产品",
    label_en="Product",
    harness_role="Product",
    skill_id="product",
    responsibilities=(
        "Verify goal attainment with concrete, user-valuable detail — not slogans",
        "Reject outline-only catalogs, placeholder copy, and shell demos",
        "Require field-level substance on domain content (points/steps/journeys)",
        "Seal product acceptance only after Live Preview evidence",
        "Own continuous follow-up when product gaps reopen",
    ),
    non_responsibilities=(
        "Write production source as the primary author",
        "Approve structure scaffolding as product-ready",
        "Waive missing acceptance criteria",
    ),
    acceptance_artifacts=(
        "detailed_acceptance_checklist",
        "content_substance_report",
        "goal_alignment_notes",
    ),
    gap_codes=(
        "delivery-product-outline",
        "preview-content-depth",
        "forbid-demo-shell",
        "forbid-placeholder-content",
        "min-visible-text",
        "goal-semantic-alignment",
    ),
)

TECH_AGENT = DeliveryRoleAgent(
    role_id="tech",
    task_type="delivery.tech.review",
    label_zh="技术",
    label_en="Tech",
    harness_role="Tech",
    skill_id="http-api",
    responsibilities=(
        "Verify public Preview API contracts under the same mount users open",
        "Confirm routes return real JSON/HTML with expected schemas",
        "Flag broken pairwise/cross-resource links and 404 catalogs",
        "Refuse soft-pass when origin-absolute /api probes escape Preview prefix",
        "Own continuous follow-up when API/runtime contracts regress",
    ),
    non_responsibilities=(
        "Self-certify as independent product acceptance",
        "Change organization topology",
        "Invent API failures not observed in evidence",
    ),
    acceptance_artifacts=(
        "api_contract_probe_report",
        "route_matrix",
        "tech_risk_notes",
    ),
    gap_codes=(
        "delivery-tech-api",
        "preview-content-depth",
        "SMOKE_FAILED",
        "preview-home-reachable",
        "preview-asset-reachability",
    ),
)

TEST_AGENT = DeliveryRoleAgent(
    role_id="test",
    task_type="delivery.test.review",
    label_zh="测试",
    label_en="Test",
    harness_role="Test",
    skill_id="test-harness",
    responsibilities=(
        "Execute a scenario matrix against Live Preview (not file-path outlines)",
        "Require reproducible evidence for each critical journey",
        "Fail closed when scenarios are missing, skipped, or only described",
        "Distinguish structure QA from behavioral/product testing",
        "Own continuous follow-up when scenario regressions appear",
    ),
    non_responsibilities=(
        "Author the artifacts under test as primary producer",
        "Treat Hive structure QA accepted=true as test pass",
        "Silently skip failing scenarios",
    ),
    acceptance_artifacts=(
        "scenario_matrix",
        "scenario_evidence",
        "regression_notes",
    ),
    gap_codes=(
        "delivery-test-scenarios",
        "preview-internal-nav",
        "preview-content-depth",
        "TEST_FAILED",
        "project-tests",
    ),
)

UX_AGENT = DeliveryRoleAgent(
    role_id="ux",
    task_type="delivery.ux.review",
    label_zh="体验",
    label_en="UX",
    harness_role="UX",
    skill_id="ui",
    responsibilities=(
        "Verify information architecture: brand, hierarchy, operable navigation",
        "Require designed surfaces (stylesheet substance + semantic main)",
        "Reject browser-default dumps and marketing-outline-only first screens",
        "Confirm list→detail journeys are clickable and readable",
        "Own continuous follow-up when UX surfaces regress",
    ),
    non_responsibilities=(
        "Approve backend-only APIs as UX complete",
        "Confuse CSS byte length alone with good UX",
        "Ship without primary navigation working on Preview URL",
    ),
    acceptance_artifacts=(
        "ux_surface_report",
        "nav_journey_evidence",
        "visual_hierarchy_notes",
    ),
    gap_codes=(
        "delivery-ux-surface",
        "stylesheet-substance",
        "styled-surface",
        "preview-internal-nav",
        "semantic-main",
        "min-visible-text",
    ),
)

OPS_AGENT = DeliveryRoleAgent(
    role_id="ops",
    task_type="delivery.ops.review",
    label_zh="运维",
    label_en="Ops",
    harness_role="Ops",
    skill_id="ops-environment",
    responsibilities=(
        "Verify host/preview environment can sustain Live Preview delivery",
        "Fail closed on disk/mem/preview-venv thrash that blocks QA",
        "Trigger or require environment heal before soft-passing Preview",
        "Own continuous follow-up when host guard regresses",
    ),
    non_responsibilities=(
        "Rewrite product copy to hide infrastructure failure",
        "Approve Preview success while host is unhealthy",
        "Disable host guard to green-pass delivery",
    ),
    acceptance_artifacts=(
        "host_health_snapshot",
        "ops_heal_receipt",
        "preview_process_notes",
    ),
    gap_codes=(
        "delivery-ops-host",
        "HOST_RESOURCE",
        "disk_percent",
        "mem_percent",
        "preview_venv_count",
        "load1_per_cpu",
    ),
)

DELIVERY_ROLE_AGENTS: tuple[DeliveryRoleAgent, ...] = (
    PRODUCT_AGENT,
    TECH_AGENT,
    TEST_AGENT,
    UX_AGENT,
    OPS_AGENT,
)

_ROLE_BY_ID = {a.role_id: a for a in DELIVERY_ROLE_AGENTS}

# Map Hive durable roles → delivery roles (who owns which swarm lane).
HIVE_TO_DELIVERY_ROLE = {
    "pm": "product",
    "dev": "tech",
    "qa": "test",  # structure QA alone is insufficient; Test swarm is authoritative
}

_OPS_ONLY_HINT = re.compile(
    r"(?:host\s*heal|environment.?heal|kswapd|preview[-_.]?venv|磁盘|运维自愈|host.?resource)",
    re.I,
)
_APP_HINT = re.compile(
    r"(?:live.?preview|/preview/|app\b|website|产品|应用|站点|crosswalk|合规|catalog|dashboard|页面|前端)",
    re.I,
)


def get_delivery_role(role_id: str) -> DeliveryRoleAgent | None:
    return _ROLE_BY_ID.get(role_id)


def select_roles_for_goal(
    goal_input: str = "",
    *,
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    """Self-supplement delivery roles from Goal signals (fixed catalog only).

    Never invent free-form roles. Always pick from ``DELIVERY_ROLE_AGENTS``.
    App / preview Goals get the full Product→Ops roster by default.
    """
    meta = metadata or {}
    explicit = meta.get("delivery_roles") or meta.get("delivery_role_roster")
    if isinstance(explicit, (list, tuple)) and explicit:
        out = [str(r).strip().lower() for r in explicit if str(r).strip()]
        return [r for r in out if r in _ROLE_BY_ID]

    text = " ".join(
        str(x or "")
        for x in (
            goal_input,
            meta.get("title"),
            meta.get("goal_kind"),
            meta.get("project_kind"),
        )
    )
    # Pure ops/environment Goals: Ops + Tech only.
    # Strip preview-venv tokens so they do not trip the app hint.
    app_text = re.sub(r"preview[-_.]?venv", " ", text, flags=re.I)
    if _OPS_ONLY_HINT.search(text) and not _APP_HINT.search(app_text):
        return ["ops", "tech"]

    # Default: Regent supplements the full delivery roster for app projects.
    return list(APP_PROJECT_ROLES)


def delivery_role_catalog(
    *,
    goal_input: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Machine-readable registry: which Agents Regent defines and selects."""
    selected = select_roles_for_goal(goal_input, metadata=metadata)
    return {
        "schema": DELIVERY_ROLE_CATALOG_SCHEMA,
        "certified_hive_template": "pm-dev-independent-qa-v1",
        "delivery_roles_template": DELIVERY_ROLES_TEMPLATE_ID,
        "certified_hive_roles": ["pm", "dev", "qa"],
        "certified_hive_gap": (
            "Certified hive has no durable UX/Ops Agents and Hive QA is "
            "structure-only. Companion template delivery-roles-v1 materializes "
            "Product/Tech/Test/UX/Ops as durable Deployments + AgentTasks."
        ),
        "self_supplement": True,
        "continuous_followup": True,
        "selected_roles": selected,
        "delivery_agents": [a.as_dict() for a in DELIVERY_ROLE_AGENTS],
        "hive_to_delivery": dict(HIVE_TO_DELIVERY_ROLE),
    }
