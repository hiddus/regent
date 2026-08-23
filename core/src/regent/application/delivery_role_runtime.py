"""Durable runtime for delivery-roles-v1 (Product/Tech/Test/UX/Ops).

Companion to certified Hive — not free-form adaptive topology. Materializes
Deployments for roles selected by ``select_roles_for_goal``, records
``delivery.*.review`` AgentTasks from Swarm evidence, and stamps role-scoped
follow-up so failed roles must re-pass.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from regent.application.agent_lifecycle_service import AgentLifecycleService
from regent.application.agent_task_service import AgentTaskService
from regent.application.delivery_framework_fix import framework_fix_plan
from regent.application.delivery_role_agents import (
    DELIVERY_ROLE_AGENTS,
    DELIVERY_ROLES_TEMPLATE_ID,
    get_delivery_role,
    select_roles_for_goal,
)
from regent.infrastructure.models import AgentSpecModel, GoalModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryRoleBinding:
    role: str
    agent_spec_id: uuid.UUID
    deployment_id: uuid.UUID
    capabilities: list[str]


@dataclass(frozen=True, slots=True)
class DeliveryRoleMaterialization:
    template_id: str
    role_ids: list[str]
    bindings: dict[str, DeliveryRoleBinding]
    organization_version_id: uuid.UUID
    plan: dict[str, Any] = field(default_factory=dict)


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def role_assignment_name(role: str) -> str:
    return f"goal-delivery-{role}"


async def materialize_delivery_roles(
    session: AsyncSession,
    *,
    goal_id: uuid.UUID,
    organization_version_id: uuid.UUID,
    goal_input: str = "",
    metadata: dict[str, Any] | None = None,
    lifecycle: AgentLifecycleService | None = None,
    actor: str = "regent-core:delivery-roles",
) -> DeliveryRoleMaterialization:
    """Create OPERATING Deployments for self-supplemented delivery roles."""
    from regent.application.policy_engine import PolicyEngine
    from regent.infrastructure.aar1_models import AgentDeploymentModel
    from sqlalchemy import select

    if lifecycle is None:
        life = object.__new__(AgentLifecycleService)
        life._sessions = None  # type: ignore[assignment]
        life._policy = PolicyEngine()
        life._enforce = True
    else:
        life = lifecycle

    role_ids = select_roles_for_goal(goal_input, metadata=metadata)
    plan = framework_fix_plan(goal_input=goal_input, metadata=metadata)

    # Reuse existing OPERATING delivery deployments when present.
    existing = list(
        await session.scalars(
            select(AgentDeploymentModel).where(
                AgentDeploymentModel.organization_version_id == organization_version_id,
                AgentDeploymentModel.goal_id == goal_id,
                AgentDeploymentModel.status == "OPERATING",
            )
        )
    )
    bindings: dict[str, DeliveryRoleBinding] = {}
    for dep in existing:
        role = str(dep.role or "")
        if role in role_ids and role not in bindings:
            bindings[role] = DeliveryRoleBinding(
                role=role,
                agent_spec_id=uuid.uuid4(),
                deployment_id=dep.id,
                capabilities=list((dep.effective_permissions_json or {}).get("allow") or []),
            )

    for role_id in role_ids:
        if role_id in bindings:
            continue
        agent = get_delivery_role(role_id)
        if agent is None:
            continue
        caps = [f"delivery.{role_id}", "preview.review"]
        agent_spec_id = uuid.uuid4()
        session.add(
            AgentSpecModel(
                id=agent_spec_id,
                name=role_assignment_name(role_id),
                version=1,
                status="ACTIVE",
                scope_goal_id=goal_id,
                capability_names=caps,
                model_ref="configured-model",
                tool_refs=[],
                constraints={
                    "goal_scope_only": True,
                    "max_delegation_depth": 0,
                    "delivery_role": role_id,
                    "template_id": DELIVERY_ROLES_TEMPLATE_ID,
                    "independent_reviewer": agent.independent_reviewer,
                    "task_type": agent.task_type,
                },
            )
        )
        await session.flush()
        manifest = {
            "schema_version": "agent-manifest/v1",
            "identity": {
                "name": role_assignment_name(role_id),
                "version": 1,
                "goal_id": str(goal_id),
                "role": role_id,
            },
            "capabilities": caps,
            "template_id": DELIVERY_ROLES_TEMPLATE_ID,
            "task_type": agent.task_type,
            "independent_reviewer": agent.independent_reviewer,
        }
        spec_ver = await life.create_spec_version(
            session,
            agent_spec_id=agent_spec_id,
            manifest=manifest,
            actor=actor,
            certify=True,
        )
        dep = await life.create_deployment(
            session,
            agent_spec_version_id=spec_ver.id,
            organization_version_id=organization_version_id,
            goal_id=goal_id,
            role=role_id,
            effective_permissions={"allow": caps, "require_permit": [], "deny": []},
            actor=actor,
        )
        for to_status, reason in (
            ("DEPLOYED", "delivery-role-materialize"),
            ("OPERATING", "delivery-role-ready"),
        ):
            await life.transition_deployment(
                session, dep.id, to_status=to_status, actor=actor, reason=reason
            )
        bindings[role_id] = DeliveryRoleBinding(
            role=role_id,
            agent_spec_id=agent_spec_id,
            deployment_id=dep.id,
            capabilities=caps,
        )

    # Relationships: test/ux/ops review tech (unidirectional, no cross-invocation).
    # REMOVED: product→tech SUPERVISES/DELEGATES_TO — these created indirect
    # agent-to-agent invocation paths that risked dead-loops and token waste.
    # All delivery roles now report only to the orchestrator (hub-and-spoke).
    try:
        tech = bindings.get("tech")
        test = bindings.get("test")
        ux = bindings.get("ux")
        ops = bindings.get("ops")
        for reviewer in (test, ux, ops):
            if reviewer and tech:
                AgentLifecycleService.assert_producer_reviewer_separation(
                    tech.deployment_id, reviewer.deployment_id
                )
                await life.add_relationship(
                    session,
                    organization_version_id=organization_version_id,
                    source_deployment_id=reviewer.deployment_id,
                    target_deployment_id=tech.deployment_id,
                    relationship_type="REVIEWS",
                )
    except Exception:
        logger.warning(
            "delivery role relationship wiring skipped",
            extra={"goal_id": str(goal_id)},
            exc_info=True,
        )

    goal = await session.get(GoalModel, goal_id)
    if goal is not None:
        meta = dict(goal.metadata_json or {})
        meta["delivery_roles_template"] = DELIVERY_ROLES_TEMPLATE_ID
        meta["delivery_role_roster"] = list(role_ids)
        meta["delivery_framework_plan"] = plan
        meta["delivery_role_bindings"] = {
            rid: {
                "deployment_id": str(b.deployment_id),
                "agent_spec_id": str(b.agent_spec_id),
                "capabilities": list(b.capabilities),
            }
            for rid, b in bindings.items()
        }
        goal.metadata_json = meta
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(goal, "metadata_json")

    return DeliveryRoleMaterialization(
        template_id=DELIVERY_ROLES_TEMPLATE_ID,
        role_ids=list(role_ids),
        bindings=bindings,
        organization_version_id=organization_version_id,
        plan=plan,
    )


async def load_delivery_role_bindings(
    session: AsyncSession,
    *,
    goal_id: uuid.UUID,
    organization_version_id: uuid.UUID,
) -> dict[str, DeliveryRoleBinding]:
    from sqlalchemy import select

    from regent.infrastructure.aar1_models import AgentDeploymentModel

    known = {a.role_id for a in DELIVERY_ROLE_AGENTS}
    deps = list(
        await session.scalars(
            select(AgentDeploymentModel).where(
                AgentDeploymentModel.organization_version_id == organization_version_id,
                AgentDeploymentModel.goal_id == goal_id,
                AgentDeploymentModel.status == "OPERATING",
            )
        )
    )
    out: dict[str, DeliveryRoleBinding] = {}
    for dep in deps:
        role = str(dep.role or "")
        if role in known:
            out[role] = DeliveryRoleBinding(
                role=role,
                agent_spec_id=uuid.uuid4(),
                deployment_id=dep.id,
                capabilities=list(
                    (dep.effective_permissions_json or {}).get("allow") or []
                ),
            )
    return out


async def record_delivery_role_reviews(
    sessions: Any,
    *,
    goal_id: uuid.UUID,
    preview_url: str,
    swarm_result: dict[str, Any],
    correlation_id: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Offer→claim→complete/fail durable AgentTasks for each Swarm role review."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from regent.infrastructure.models import OrganizationModel

    corr = correlation_id or f"delivery-swarm:{goal_id}:{attempt}"
    receipts: list[dict[str, Any]] = []
    followup: list[str] = []

    bindings: dict[str, DeliveryRoleBinding] = {}
    org_version_id: uuid.UUID | None = None
    binding_reload_required = False
    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            return {"status": "NO_GOAL", "receipts": [], "followup_roles": []}
        org = await session.scalar(
            select(OrganizationModel).where(OrganizationModel.goal_id == goal_id)
        )
        org_version_id = org.current_version_id if org is not None else None
        goal_input = str(goal.original_input or "")
        meta = dict(goal.metadata_json or {})
        if org_version_id is not None:
            try:
                mat = await materialize_delivery_roles(
                    session,
                    goal_id=goal_id,
                    organization_version_id=org_version_id,
                    goal_input=goal_input,
                    metadata=meta,
                )
                bindings = mat.bindings
            except Exception:
                logger.warning(
                    "delivery role materialize during record failed",
                    extra={"goal_id": str(goal_id)},
                    exc_info=True,
                )
                # The failed flush may have invalidated this transaction. Do
                # not issue another query through the closed context manager.
                binding_reload_required = True

    if binding_reload_required and org_version_id is not None:
        async with sessions() as session:
            bindings = await load_delivery_role_bindings(
                session,
                goal_id=goal_id,
                organization_version_id=org_version_id,
            )

    # Separate transactions: AgentTaskService methods open their own sessions.
    tasks = AgentTaskService(sessions)
    role_rows = list(swarm_result.get("roles") or [])
    product = bindings.get("product")
    source_id = (
        product.deployment_id
        if product is not None
        else (next(iter(bindings.values())).deployment_id if bindings else None)
    )

    for row in role_rows:
        if not isinstance(row, dict):
            continue
        role_id = str(row.get("role_id") or "")
        agent = get_delivery_role(role_id)
        binding = bindings.get(role_id)
        accepted = bool(row.get("accepted"))
        if not accepted:
            followup.append(role_id)

        receipt: dict[str, Any] = {
            "role_id": role_id,
            "accepted": accepted,
            "gaps": list(row.get("gaps") or [])[:12],
            "task_type": agent.task_type if agent else f"delivery.{role_id}.review",
            "durable": False,
        }

        if (
            agent is not None
            and binding is not None
            and org_version_id is not None
            and source_id is not None
        ):
            try:
                payload = {
                    "goal_id": str(goal_id),
                    "role": role_id,
                    "preview_url": preview_url,
                    "attempt": attempt,
                    "accepted": accepted,
                }
                view = await tasks.offer_task(
                    goal_id=goal_id,
                    organization_version_id=org_version_id,
                    source_deployment_id=source_id,
                    target_deployment_id=binding.deployment_id,
                    task_type=agent.task_type,
                    idempotency_key=f"delivery:{role_id}:{goal_id}:{attempt}",
                    payload_digest=_digest(payload),
                    capability_scope=list(binding.capabilities),
                    correlation_id=corr,
                )
                claimed = await tasks.claim_task(
                    view.id,
                    worker_id=f"delivery-swarm:{role_id}",
                )
                lease = claimed.lease_token or ""
                await tasks.start_task(view.id, lease_token=lease)
                result_ref = f"delivery:{role_id}:{goal_id}:{attempt}"
                if accepted:
                    await tasks.complete_task(
                        view.id,
                        lease_token=lease,
                        result_ref=result_ref,
                    )
                    receipt["task_status"] = "SUCCEEDED"
                else:
                    await tasks.fail_task(
                        view.id,
                        lease_token=lease,
                        error_code="DELIVERY_ROLE_REJECTED",
                        retryable=True,
                    )
                    receipt["task_status"] = "FAILED"
                receipt["durable"] = True
                receipt["task_id"] = str(view.id)
            except Exception as exc:
                logger.warning(
                    "delivery role durable task record failed",
                    extra={"goal_id": str(goal_id), "role": role_id},
                    exc_info=True,
                )
                receipt["task_status"] = "RECORD_ERROR"
                receipt["error"] = f"{type(exc).__name__}: {exc}"[:240]
        else:
            receipt["task_status"] = "NO_DEPLOYMENT"
        receipts.append(receipt)

    followup = list(dict.fromkeys(followup))
    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is not None:
            meta = dict(goal.metadata_json or {})
            meta["delivery_role_task_receipts"] = receipts
            meta["delivery_role_followup"] = {
                "required_roles": followup,
                "reason": (
                    "Failed delivery roles must re-pass Live Preview swarm before "
                    "PREVIEW_SUCCEEDED"
                    if followup
                    else "all_selected_roles_passed"
                ),
                "attempt": attempt,
            }
            if followup:
                meta["session_steer_brief"] = (
                    "【Delivery Role 持续跟进 — 失败角色必须复验通过】\n"
                    + "\n".join(f"- 角色 {r} 未通过，禁止大纲放行" for r in followup)
                    + "\n先修该角色验收项，再重新跑 Product/Tech/Test/UX/Ops Swarm。"
                )[:4000]
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")

    return {
        "status": "RECORDED",
        "receipts": receipts,
        "followup_roles": followup,
        "template_id": DELIVERY_ROLES_TEMPLATE_ID,
    }


async def evolve_failed_delivery_roles(
    sessions: Any,
    *,
    goal_id: uuid.UUID,
    swarm_result: dict[str, Any],
    preview_url: str,
    workspace_root: Any,
    provider: Any,
) -> list[dict[str, Any]]:
    """Role-scoped harness evolution: each failed role owns its skill lesson."""
    from pathlib import Path

    from regent.application.harness_evolution import HarnessEvolutionService
    from sqlalchemy.orm.attributes import flag_modified

    svc = HarnessEvolutionService(provider, workspace_root=Path(workspace_root))
    evolutions: list[dict[str, Any]] = []
    for row in list(swarm_result.get("roles") or []):
        if not isinstance(row, dict) or bool(row.get("accepted")):
            continue
        role_id = str(row.get("role_id") or "")
        agent = get_delivery_role(role_id)
        if agent is None:
            continue
        gaps = list(row.get("gaps") or []) or [f"delivery-{role_id}"]
        receipt = await svc.evolve_from_gaps(
            gaps=gaps[:12],
            actor=f"regent-core:delivery-{role_id}",
            goal_context=(
                f"Delivery role {role_id} rejected preview; "
                f"findings={list(row.get('findings') or [])[:6]}"
            )[:4000],
            preview_url=preview_url or None,
            preferred_skill_id=agent.skill_id,
        )
        payload = receipt.as_dict()
        payload["delivery_role"] = role_id
        payload["harness_role"] = agent.harness_role
        evolutions.append(payload)

    if evolutions:
        async with sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is not None:
                meta = dict(goal.metadata_json or {})
                meta["delivery_role_evolution"] = evolutions
                goal.metadata_json = meta
                flag_modified(goal, "metadata_json")
    return evolutions
