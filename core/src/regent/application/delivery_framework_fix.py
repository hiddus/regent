"""Regent framework fix plan owned by Product / Tech / UX / Test / Ops.

This is an **executable contract**, not a slide deck. Each phase has an owner
role, acceptance criteria, and a code landing target. Continuous follow-up is
mandatory: role failure → harness lesson → same-role re-verify.

Problem statement (why this exists)
-----------------------------------
Certified Hive only materializes pm/dev/qa. Delivery Role Swarm was a hard gate
without durable AgentTasks. Roles were not self-supplemented from the Goal, and
evolution did not force the failing role to re-run. That produced outline-only
"passes" and failed Regent's core delivery goal.
"""

from __future__ import annotations

from typing import Any

from regent.application.delivery_role_agents import (
    DELIVERY_ROLES_TEMPLATE_ID,
    select_roles_for_goal,
)

FRAMEWORK_FIX_PLAN_VERSION = "delivery-framework-fix/v1"


def framework_fix_plan(*, goal_input: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the multi-role repair plan Regent applies for a Goal."""
    roles = select_roles_for_goal(goal_input, metadata=metadata)
    return {
        "schema": FRAMEWORK_FIX_PLAN_VERSION,
        "template_id": DELIVERY_ROLES_TEMPLATE_ID,
        "problem": (
            "Hive structure path alone cannot certify complete delivery; "
            "Product/Tech/Test/UX/Ops must follow the Goal as durable Agents, "
            "self-supplement from project signals, and continuously re-verify."
        ),
        "selected_roles": roles,
        "principles": [
            "Self-supplement roles from Goal signals (never ship with missing Test/UX/Ops on app projects)",
            "Durable AgentTask per role review — not prompt-only shells",
            "Fail closed on outline/shell; Live Preview evidence required",
            "Failed role owns evolution; same role must re-pass before acceptance",
            "Fixed companion template (not free-form adaptive topology)",
        ],
        "phases": [
            {
                "id": "P0-catalog",
                "owner": "product",
                "title": "Define and self-supplement delivery Agents",
                "acceptance": [
                    "Catalog includes product/tech/test/ux/ops",
                    "select_roles_for_goal returns full app roster by default",
                ],
                "landing": [
                    "delivery_role_agents.py",
                    "delivery_framework_fix.py",
                ],
            },
            {
                "id": "P1-materialize",
                "owner": "tech",
                "title": "Materialize durable Deployments for selected roles",
                "acceptance": [
                    "Organization version has OPERATING deployments for selected roles",
                    "Goal metadata records delivery_role_roster",
                ],
                "landing": [
                    "delivery_role_runtime.py",
                    "organization_service.py",
                ],
            },
            {
                "id": "P2-verify-loop",
                "owner": "test",
                "title": "Wire delivery.*.review AgentTasks to Live Preview swarm",
                "acceptance": [
                    "Each role review is offer→claim→complete/fail with evidence",
                    "Swarm rejection cannot soft-pass Preview success",
                ],
                "landing": [
                    "delivery_role_swarm.py",
                    "delivery_role_runtime.py",
                    "execution_orchestrator.py",
                ],
            },
            {
                "id": "P3-ux-substance",
                "owner": "ux",
                "title": "UX owns designed surface + operable journeys",
                "acceptance": [
                    "UX gap codes block PREVIEW_SUCCEEDED",
                    "Outline-only first screens fail delivery-ux-surface",
                ],
                "landing": ["delivery_role_swarm.py", "live_preview_qa.py"],
            },
            {
                "id": "P4-ops-guard",
                "owner": "ops",
                "title": "Ops owns host health for Preview delivery",
                "acceptance": [
                    "Ops role runs on app projects",
                    "HOST_RESOURCE / disk / preview_venv gaps map to ops-environment",
                ],
                "landing": [
                    "delivery_role_agents.py",
                    "delivery_role_swarm.py",
                    "host_resources.py",
                ],
            },
            {
                "id": "P5-evolve-reverify",
                "owner": "product",
                "title": "Role-scoped harness evolution + forced re-verify",
                "acceptance": [
                    "Harness role labels include Product|Tech|Test|UX|Ops|PM",
                    "Goal stamps delivery_role_followup requiring failed roles to re-pass",
                ],
                "landing": [
                    "harness_evolution.py",
                    "delivery_role_runtime.py",
                    "execution_orchestrator.py",
                ],
            },
        ],
        "done_when": [
            "Goal metadata.delivery_agents_defined lists all selected roles",
            "Preview success requires Delivery Role Swarm accepted=true",
            "Failed roles leave durable AgentTask FAIL evidence + followup roster",
        ],
    }
