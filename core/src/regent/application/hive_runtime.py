"""Certified Durable Hive runtime helpers (pm-dev-independent-qa-v1).

Opt-in fixed template only — not adaptive free-form multi-agent
(ROLLOUT_NOT_ALLOWED). Materializes SpecVersion/Deployment/Relationship and
offers durable PM→Dev→QA AgentTasks for the main execute/review path.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from regent.application.aar1_contract import CERTIFIED_HIVE_TEMPLATE_ID
from regent.application.agent_lifecycle_service import AgentLifecycleService
from regent.application.agent_task_service import AgentTaskService, AgentTaskView
from regent.infrastructure.models import AgentSpecModel, AssignmentModel, WorkModel

HIVE_TASK_TYPES = {
    "pm": "hive.pm.plan",
    "dev": "hive.dev.execute",
    "qa": "hive.qa.review",
}


@dataclass(frozen=True, slots=True)
class HiveRoleBinding:
    role: str
    agent_spec_id: uuid.UUID
    deployment_id: uuid.UUID
    capabilities: list[str]


@dataclass(frozen=True, slots=True)
class HiveMaterialization:
    template_id: str
    bindings: dict[str, HiveRoleBinding]
    organization_version_id: uuid.UUID


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def role_assignment_name(role: str) -> str:
    return f"goal-hive-{role}"


def agent_spec_ref_for_role(role: str, template_id: str = CERTIFIED_HIVE_TEMPLATE_ID) -> str:
    return f"{template_id}:{role}"


async def materialize_hive_topology(
    session: AsyncSession,
    *,
    goal_id: uuid.UUID,
    organization_id: uuid.UUID,
    organization_version_id: uuid.UUID,
    topology: dict[str, Any],
    works: list[WorkModel],
    gaps: list[str],
    lifecycle: AgentLifecycleService | None = None,
    actor: str = "regent-core",
) -> HiveMaterialization:
    """Create PM/Dev/QA AgentSpec + CERTIFIED SpecVersion + OPERATING Deployment."""
    from regent.application.policy_engine import PolicyEngine

    if lifecycle is None:
        life = object.__new__(AgentLifecycleService)
        life._sessions = None  # type: ignore[assignment]
        life._policy = PolicyEngine()
        life._enforce = True
    else:
        life = lifecycle

    template_id = str(topology.get("template_id") or CERTIFIED_HIVE_TEMPLATE_ID)
    roles = list(topology.get("roles") or [])
    bindings: dict[str, HiveRoleBinding] = {}

    for role_spec in roles:
        role = str(role_spec.get("role") or "executor")
        caps = [
            str(c)
            for c in (
                role_spec.get("capabilities")
                or role_spec.get("capability_requirements")
                or []
            )
        ]
        agent_spec_id = uuid.uuid4()
        depth = int(role_spec.get("max_delegation_depth") or 1)
        session.add(
            AgentSpecModel(
                id=agent_spec_id,
                name=role_assignment_name(role),
                version=1,
                status="CANDIDATE" if gaps else "ACTIVE",
                scope_goal_id=goal_id,
                capability_names=caps,
                model_ref="configured-model",
                tool_refs=[],
                constraints={
                    "goal_scope_only": True,
                    "max_delegation_depth": depth,
                    "hive_role": role,
                    "template_id": template_id,
                    "independent_reviewer": bool(role_spec.get("independent_reviewer")),
                },
            )
        )
        await session.flush()
        manifest = {
            "schema_version": "agent-manifest/v1",
            "identity": {
                "name": role_assignment_name(role),
                "version": 1,
                "goal_id": str(goal_id),
                "role": role,
            },
            "capabilities": caps,
            "template_id": template_id,
            "independent_reviewer": bool(role_spec.get("independent_reviewer")),
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
            role=role,
            effective_permissions={
                "allow": caps,
                "require_permit": [],
                "deny": [],
            },
            actor=actor,
        )
        for to_status, reason in (
            ("DEPLOYED", "hive-materialize"),
            ("OPERATING", "hive-ready"),
        ):
            await life.transition_deployment(
                session, dep.id, to_status=to_status, actor=actor, reason=reason
            )
        bindings[role] = HiveRoleBinding(
            role=role,
            agent_spec_id=agent_spec_id,
            deployment_id=dep.id,
            capabilities=caps,
        )

    # Relationships: PM supervises/delegates Dev; QA reviews/approves Dev (FR-AAR-012).
    pm = bindings.get("pm")
    dev = bindings.get("dev")
    qa = bindings.get("qa")
    if pm and dev:
        await life.add_relationship(
            session,
            organization_version_id=organization_version_id,
            source_deployment_id=pm.deployment_id,
            target_deployment_id=dev.deployment_id,
            relationship_type="SUPERVISES",
        )
        await life.add_relationship(
            session,
            organization_version_id=organization_version_id,
            source_deployment_id=pm.deployment_id,
            target_deployment_id=dev.deployment_id,
            relationship_type="DELEGATES_TO",
        )
    if qa and dev:
        AgentLifecycleService.assert_producer_reviewer_separation(
            dev.deployment_id, qa.deployment_id
        )
        await life.add_relationship(
            session,
            organization_version_id=organization_version_id,
            source_deployment_id=qa.deployment_id,
            target_deployment_id=dev.deployment_id,
            relationship_type="REVIEWS",
        )
        await life.add_relationship(
            session,
            organization_version_id=organization_version_id,
            source_deployment_id=qa.deployment_id,
            target_deployment_id=dev.deployment_id,
            relationship_type="APPROVES",
        )

    # Assignments: DB unique is (organization_id, work_id) — one row per work.
    # Hive PM/Dev/QA collaboration is carried by deployments, relationships, and
    # durable hive tasks; Dev (or executor) is the primary work assignee.
    if not bindings:
        raise ValueError("hive materialize requires at least one role binding")
    primary = bindings.get("dev") or bindings.get("executor") or next(iter(bindings.values()))
    for work in works:
        session.add(
            AssignmentModel(
                id=uuid.uuid4(),
                organization_id=organization_id,
                work_id=work.id,
                agent_spec_id=primary.agent_spec_id,
                role=primary.role,
                delegated_capabilities=list(
                    work.metadata_json.get(
                        "required_capabilities", primary.capabilities
                    )
                ),
            )
        )

    return HiveMaterialization(
        template_id=template_id,
        bindings=bindings,
        organization_version_id=organization_version_id,
    )


@dataclass(frozen=True, slots=True)
class HiveTaskChain:
    pm_task: AgentTaskView | None
    dev_task: AgentTaskView
    qa_task: AgentTaskView
    producer_ref: str
    reviewer_ref: str


async def offer_hive_task_chain(
    tasks: AgentTaskService,
    *,
    goal_id: uuid.UUID,
    work_id: uuid.UUID,
    organization_version_id: uuid.UUID,
    bindings: dict[str, HiveRoleBinding],
    correlation_id: str,
    attempt: int,
    session: AsyncSession | None = None,
) -> HiveTaskChain:
    """Offer durable PM→Dev→QA tasks (idempotent per work attempt)."""
    pm = bindings.get("pm")
    dev = bindings["dev"] if "dev" in bindings else bindings.get("executor")
    qa = bindings.get("qa")
    if dev is None or qa is None:
        raise ValueError("hive topology requires dev (or executor) and qa deployments")

    AgentLifecycleService.assert_producer_reviewer_separation(
        dev.deployment_id, qa.deployment_id
    )

    source_pm = pm.deployment_id if pm is not None else dev.deployment_id
    payload = {
        "goal_id": str(goal_id),
        "work_id": str(work_id),
        "attempt": attempt,
        "template_id": CERTIFIED_HIVE_TEMPLATE_ID,
    }
    digest = _digest(payload)

    pm_view: AgentTaskView | None = None
    if pm is not None:
        pm_view = await tasks.offer_task(
            goal_id=goal_id,
            organization_version_id=organization_version_id,
            source_deployment_id=source_pm,
            target_deployment_id=pm.deployment_id,
            task_type=HIVE_TASK_TYPES["pm"],
            idempotency_key=f"hive:pm:{work_id}:{attempt}",
            payload_digest=digest,
            capability_scope=list(pm.capabilities),
            correlation_id=correlation_id,
            work_id=work_id,
            session=session,
        )

    parent_id = pm_view.id if pm_view is not None else None
    dev_view = await tasks.offer_task(
        goal_id=goal_id,
        organization_version_id=organization_version_id,
        source_deployment_id=source_pm,
        target_deployment_id=dev.deployment_id,
        task_type=HIVE_TASK_TYPES["dev"],
        idempotency_key=f"hive:dev:{work_id}:{attempt}",
        payload_digest=digest,
        capability_scope=list(dev.capabilities),
        correlation_id=correlation_id,
        work_id=work_id,
        parent_task_id=parent_id,
        causation_id=str(parent_id) if parent_id else None,
        session=session,
    )
    qa_view = await tasks.offer_task(
        goal_id=goal_id,
        organization_version_id=organization_version_id,
        source_deployment_id=dev.deployment_id,
        target_deployment_id=qa.deployment_id,
        task_type=HIVE_TASK_TYPES["qa"],
        idempotency_key=f"hive:qa:{work_id}:{attempt}",
        payload_digest=digest,
        capability_scope=list(qa.capabilities),
        correlation_id=correlation_id,
        work_id=work_id,
        parent_task_id=dev_view.id,
        causation_id=str(dev_view.id),
        session=session,
    )
    return HiveTaskChain(
        pm_task=pm_view,
        dev_task=dev_view,
        qa_task=qa_view,
        producer_ref=agent_spec_ref_for_role(dev.role),
        reviewer_ref=agent_spec_ref_for_role(qa.role),
    )


async def load_operating_hive_bindings(
    session: AsyncSession,
    *,
    organization_version_id: uuid.UUID,
) -> dict[str, HiveRoleBinding]:
    from sqlalchemy import select

    from regent.infrastructure.aar1_models import AgentDeploymentModel

    deps = list(
        await session.scalars(
            select(AgentDeploymentModel).where(
                AgentDeploymentModel.organization_version_id == organization_version_id,
                AgentDeploymentModel.status == "OPERATING",
            )
        )
    )
    return {
        dep.role: HiveRoleBinding(
            role=dep.role,
            agent_spec_id=uuid.uuid4(),
            deployment_id=dep.id,
            capabilities=list((dep.effective_permissions_json or {}).get("allow") or []),
        )
        for dep in deps
    }


async def maybe_offer_generation_hive_chain(
    sessions: Any,
    *,
    goal_id: uuid.UUID,
    generation_run_id: uuid.UUID,
    correlation_id: str,
    attempt: int = 1,
) -> HiveTaskChain | None:
    """Offer PM→Dev→QA AgentTasks for a generation run when hive org is active.

    ``work_id`` is left unset (generation runs are not Work rows); idempotency
    keys use the generation_run_id instead.
    """
    from sqlalchemy import select

    from regent.application.aar1_contract import is_certified_hive_topology
    from regent.infrastructure.aar1_models import OrganizationVersionModel
    from regent.infrastructure.models import OrganizationModel

    async with sessions() as session:
        org = await session.scalar(
            select(OrganizationModel).where(OrganizationModel.goal_id == goal_id)
        )
        if org is None or org.current_version_id is None:
            return None
        version = await session.get(OrganizationVersionModel, org.current_version_id)
        topology = dict((version.topology_json if version else {}) or {})
        if not is_certified_hive_topology(topology):
            return None
        bindings = await load_operating_hive_bindings(
            session, organization_version_id=org.current_version_id
        )
        if "qa" not in bindings or ("dev" not in bindings and "executor" not in bindings):
            return None
        org_version_id = org.current_version_id

    tasks = AgentTaskService(sessions)
    async with sessions() as session, session.begin():
        # Reuse offer_hive_task_chain with generation_run_id as synthetic work key
        # for idempotency only; do not set work_id FK (not a works row).
        pm = bindings.get("pm")
        dev = bindings["dev"] if "dev" in bindings else bindings["executor"]
        qa = bindings["qa"]
        AgentLifecycleService.assert_producer_reviewer_separation(
            dev.deployment_id, qa.deployment_id
        )
        source_pm = pm.deployment_id if pm is not None else dev.deployment_id
        payload = {
            "goal_id": str(goal_id),
            "generation_run_id": str(generation_run_id),
            "attempt": attempt,
            "template_id": CERTIFIED_HIVE_TEMPLATE_ID,
        }
        digest = _digest(payload)
        pm_view: AgentTaskView | None = None
        if pm is not None:
            pm_view = await tasks.offer_task(
                goal_id=goal_id,
                organization_version_id=org_version_id,
                source_deployment_id=source_pm,
                target_deployment_id=pm.deployment_id,
                task_type=HIVE_TASK_TYPES["pm"],
                idempotency_key=f"hive:pm:gen:{generation_run_id}:{attempt}",
                payload_digest=digest,
                capability_scope=list(pm.capabilities),
                correlation_id=correlation_id,
                work_id=None,
                session=session,
            )
        parent_id = pm_view.id if pm_view is not None else None
        dev_view = await tasks.offer_task(
            goal_id=goal_id,
            organization_version_id=org_version_id,
            source_deployment_id=source_pm,
            target_deployment_id=dev.deployment_id,
            task_type=HIVE_TASK_TYPES["dev"],
            idempotency_key=f"hive:dev:gen:{generation_run_id}:{attempt}",
            payload_digest=digest,
            capability_scope=list(dev.capabilities),
            correlation_id=correlation_id,
            work_id=None,
            parent_task_id=parent_id,
            causation_id=str(parent_id) if parent_id else None,
            session=session,
        )
        qa_view = await tasks.offer_task(
            goal_id=goal_id,
            organization_version_id=org_version_id,
            source_deployment_id=dev.deployment_id,
            target_deployment_id=qa.deployment_id,
            task_type=HIVE_TASK_TYPES["qa"],
            idempotency_key=f"hive:qa:gen:{generation_run_id}:{attempt}",
            payload_digest=digest,
            capability_scope=list(qa.capabilities),
            correlation_id=correlation_id,
            work_id=None,
            parent_task_id=dev_view.id,
            causation_id=str(dev_view.id),
            session=session,
        )
        from regent.application.dispatch_decision import (
            DispatchDecisionInput,
            build_dispatch_record,
        )
        from regent.infrastructure.models import DispatchDecisionModel

        base_dispatch_payload = dict(payload)
        all_candidates = [str(binding.deployment_id) for binding in bindings.values()]
        audit_steps = []
        if pm is not None and pm_view is not None:
            audit_steps.append(("pm", source_pm, pm.deployment_id, pm.capabilities))
        audit_steps.extend(
            [
                ("dev", source_pm, dev.deployment_id, dev.capabilities),
                ("qa", dev.deployment_id, qa.deployment_id, qa.capabilities),
            ]
        )
        for role, source_id, selected_id, capabilities in audit_steps:
            decision_payload = build_dispatch_record(
                DispatchDecisionInput(
                    goal_id=goal_id,
                    run_id=None,
                    step_id=f"hive:gen:{generation_run_id}:{attempt}:{role}",
                    organization_version_id=org_version_id,
                    source_agent_id=str(source_id),
                    selected_agent_id=str(selected_id),
                    candidate_agent_ids=all_candidates,
                    candidate_weights={
                        candidate: (1.0 if candidate == str(selected_id) else 0.0)
                        for candidate in all_candidates
                    },
                    reason_code=f"CERTIFIED_HIVE_{role.upper()}",
                    capability_scope=list(capabilities),
                    input_payload=base_dispatch_payload,
                    output_summary={"task_role": role, "selected": str(selected_id)},
                )
            )
            session.add(DispatchDecisionModel(**decision_payload))
        return HiveTaskChain(
            pm_task=pm_view,
            dev_task=dev_view,
            qa_task=qa_view,
            producer_ref=agent_spec_ref_for_role(dev.role),
            reviewer_ref=agent_spec_ref_for_role(qa.role),
        )
