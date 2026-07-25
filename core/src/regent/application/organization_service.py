from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AgentSpecModel,
    AssignmentModel,
    CapabilityModel,
    GoalModel,
    OrganizationModel,
    WorkModel,
)

# ---------------------------------------------------------------------------
# V3 Utility Function  U(O_t | G, C, V, R_t, S_t)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UtilityWeights:
    """Weights for the six-dimension utility function (sum to 1.0)."""

    success_probability: float = 0.30
    cost: float = 0.20
    latency: float = 0.15
    human_burden: float = 0.15
    risk: float = 0.15
    explainability: float = 0.05

    def weighted_sum(self, components: dict[str, float]) -> float:
        """Compute weighted utility from component scores (each 0..1)."""
        mapping = {
            "success_probability": self.success_probability,
            "cost": self.cost,
            "latency": self.latency,
            "human_burden": self.human_burden,
            "risk": self.risk,
            "explainability": self.explainability,
        }
        return sum(mapping.get(k, 0.0) * v for k, v in components.items())


DEFAULT_WEIGHTS = UtilityWeights()
SAFETY_FIRST_WEIGHTS = UtilityWeights(
    success_probability=0.20, cost=0.10, latency=0.10,
    human_burden=0.10, risk=0.40, explainability=0.10,
)
COST_EFFICIENT_WEIGHTS = UtilityWeights(
    success_probability=0.25, cost=0.35, latency=0.20,
    human_burden=0.10, risk=0.05, explainability=0.05,
)


@dataclass(frozen=True, slots=True)
class UtilityResult:
    """Result of utility evaluation for one organization candidate."""

    utility: float
    components: dict[str, float]
    weights: UtilityWeights
    rationale: str


# V3 Utility Function types (compute_utility defined after OrganizationTemplate)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OrganizationSpace — candidate organisation architecture space (v1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRoleSpec:
    """A single role within an organisation template."""

    role: str  # executor | pm | dev | qa | coordinator
    capability_requirements: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrganizationTemplate:
    """A named, reusable organisation topology template."""

    template_id: str  # e.g. "single-agent-v1", "pm-dev-qa-v1"
    label: str  # human-readable name
    strategy: str  # SINGLE_AGENT | FIXED_TEMPLATE
    roles: list[AgentRoleSpec] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True, slots=True)
class OrganizationCandidate:
    """A concrete organisation proposal for a specific Goal, with utility estimate."""

    template: OrganizationTemplate
    goal_id: uuid.UUID
    estimated_utility: float = 0.0  # U(O_t) — placeholder for v1
    utility_breakdown: dict[str, float] = field(default_factory=dict)
    # cost, latency, human_labor, risk — populated in Phase 1b


# Default candidate templates (v1).
_V1_TEMPLATES: list[OrganizationTemplate] = [
    OrganizationTemplate(
        template_id="single-agent-v1",
        label="单 Agent",
        strategy="SINGLE_AGENT",
        roles=[
            AgentRoleSpec(
                role="executor",
                capability_requirements=[],
                constraints={"goal_scope_only": True, "max_delegation_depth": 0},
            )
        ],
        description="默认最小组织：单一 Agent 负责规划、生成、验证全部工作。",
    ),
    OrganizationTemplate(
        template_id="pm-dev-qa-v1",
        label="PM + Dev + QA 固定模板",
        strategy="FIXED_TEMPLATE",
        roles=[
            AgentRoleSpec(
                role="pm",
                capability_requirements=["delivery-review-v1"],
                constraints={"goal_scope_only": True, "max_delegation_depth": 1},
            ),
            AgentRoleSpec(
                role="dev",
                capability_requirements=["product-surface-v1"],
                constraints={"goal_scope_only": True, "max_delegation_depth": 1},
            ),
            AgentRoleSpec(
                role="qa",
                capability_requirements=["delivery-review-v1", "allowlisted-http-source-v1"],
                constraints={"goal_scope_only": True, "max_delegation_depth": 1},
            ),
        ],
        description=(
            "固定三角色模板：PM 负责需求评审与验收，Dev 负责产品构建，"
            "QA 负责交付审查与证据采集。适合 LARGE 目标。"
        ),
    ),
]


def default_organization_space() -> list[OrganizationTemplate]:
    """Return the v1 organisation candidate space (immutable snapshot)."""
    return list(_V1_TEMPLATES)


def compute_utility(
    template: OrganizationTemplate,
    *,
    goal_status: str = "ACTIVE",
    capability_gaps: list[str] | None = None,
    agent_count: int = 1,
    has_permit: bool = False,
    has_human_task: bool = False,
    estimated_cost: float = 0.0,
    estimated_latency: float = 0.0,
    weights: UtilityWeights = DEFAULT_WEIGHTS,
) -> UtilityResult:
    """Evaluate U(O_t | G, C, V, R_t, S_t) for a candidate organization."""
    gaps = capability_gaps or []
    gap_ratio = len(gaps) / max(agent_count + len(gaps), 1)
    success_prob = 1.0 - 0.5 * gap_ratio
    if template.strategy == "FIXED_TEMPLATE" and len(template.roles) >= 3:
        success_prob = min(1.0, success_prob + 0.10)

    cost_score = max(0.0, 1.0 - min(estimated_cost, 1.0))
    if template.strategy == "SINGLE_AGENT":
        cost_score = min(1.0, cost_score + 0.15)

    latency_score = max(0.0, 1.0 - min(estimated_latency, 1.0))
    if template.strategy == "SINGLE_AGENT":
        latency_score = min(1.0, latency_score + 0.10)

    human_score = 1.0
    if has_human_task:
        human_score -= 0.30
    if has_permit:
        human_score -= 0.10
    human_score = max(0.0, human_score)

    risk_score = 1.0 - 0.15 * len(gaps) - 0.10 * (1 if has_permit else 0)
    risk_score = max(0.0, min(1.0, risk_score))

    explain_score = 1.0 - 0.15 * (agent_count - 1)
    if template.strategy == "SINGLE_AGENT":
        explain_score = 1.0
    explain_score = max(0.0, min(1.0, explain_score))

    components = {
        "success_probability": round(success_prob, 4),
        "cost": round(cost_score, 4),
        "latency": round(latency_score, 4),
        "human_burden": round(human_score, 4),
        "risk": round(risk_score, 4),
        "explainability": round(explain_score, 4),
    }
    utility = round(weights.weighted_sum(components), 4)
    rationale = (
        f"U({template.template_id})={utility}: "
        + ", ".join(f"{k}={v}" for k, v in components.items())
    )
    return UtilityResult(
        utility=utility, components=components, weights=weights, rationale=rationale
    )


def select_best_organization(
    candidates: list[tuple[OrganizationTemplate, UtilityResult]],
) -> tuple[OrganizationTemplate, UtilityResult] | None:
    """Return the candidate with highest utility (argmax O_t)."""
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[1].utility)


@dataclass(frozen=True, slots=True)
class OrganizationReceipt:
    organization_id: uuid.UUID
    goal_id: uuid.UUID
    strategy: str
    agent_spec_ids: list[uuid.UUID]
    required_capabilities: list[str]
    reused_capabilities: list[str]
    capability_gaps: list[str]
    assignment_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ReorganizationResult:
    receipt: OrganizationReceipt
    recovery_work_id: uuid.UUID
    gap_kind: str
    method: str
    attempt: int


class OrganizationService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def reorganize_for_gap(
        self,
        session: AsyncSession,
        *,
        goal_id: uuid.UUID,
        gap_kind: str,
        method: str,
        capability_names: list[str],
        attempt: int,
        actor: str = "regent-core",
    ) -> ReorganizationResult:
        """GAC-D3 / ATTRIBUTE_4: adjust minimal human-machine org when Goal not attained.

        Updates the goal's organization in place (one org per goal): escalate strategy,
        add a gap-specialist agent, and assign a recovery work item.
        """
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")

        caps = sorted({str(c).strip() for c in capability_names if str(c).strip()})
        works = list(await session.scalars(select(WorkModel).where(WorkModel.goal_id == goal_id)))
        org = await session.scalar(
            select(OrganizationModel).where(OrganizationModel.goal_id == goal_id)
        )

        specialist_name = f"gap-specialist-{gap_kind}"
        specialist_id = uuid.uuid4()
        existing_specialist = await session.scalar(
            select(AgentSpecModel).where(
                AgentSpecModel.scope_goal_id == goal_id,
                AgentSpecModel.name == specialist_name,
            )
        )
        if existing_specialist is not None:
            specialist_id = existing_specialist.id
            existing_specialist.status = "ACTIVE"
            existing_specialist.capability_names = caps or list(
                existing_specialist.capability_names or []
            )
            existing_specialist.constraints = {
                **dict(existing_specialist.constraints or {}),
                "gap_kind": gap_kind,
                "escalation_method": method,
                "reorganized_by": actor,
                "attempt": attempt,
            }
        else:
            session.add(
                AgentSpecModel(
                    id=specialist_id,
                    name=specialist_name,
                    version=max(1, attempt),
                    status="ACTIVE",
                    scope_goal_id=goal_id,
                    capability_names=caps,
                    model_ref="configured-model",
                    tool_refs=[],
                    constraints={
                        "goal_scope_only": True,
                        "max_delegation_depth": 1,
                        "gap_kind": gap_kind,
                        "escalation_method": method,
                        "reorganized_by": actor,
                        "attempt": attempt,
                    },
                )
            )

        recovery_work = WorkModel(
            id=uuid.uuid4(),
            goal_id=goal_id,
            purpose=f"attainment-reorg:{gap_kind}:{method}:a{attempt}",
            input_refs=[],
            acceptance_criteria={
                "gap_kind": gap_kind,
                "method": method,
                "definition": "REGENT-DEFINITION-1.0 ATTRIBUTE_3/4",
            },
            dependency_ids=[],
            priority=10,
            budget={},
            status="PLANNED",
            version=0,
            correlation_id=goal.correlation_id,
            metadata_json={
                "required_capabilities": caps,
                "gap_kind": gap_kind,
                "escalation_method": method,
                "reorg": True,
            },
        )
        session.add(recovery_work)
        await session.flush()

        strategy = "CAPABILITY_ESCALATION" if method in {"COMPOSE", "BUILD"} else "SINGLE_AGENT"
        rationale = (
            f"Goal not attained ({gap_kind}); ATTRIBUTE_4 reorganized for {method} "
            f"attempt={attempt} with specialist '{specialist_name}'."
        )
        if org is None:
            org_id = uuid.uuid4()
            coordinator_id = uuid.uuid4()
            session.add(
                AgentSpecModel(
                    id=coordinator_id,
                    name="goal-single-agent",
                    version=1,
                    status="ACTIVE",
                    scope_goal_id=goal_id,
                    capability_names=caps,
                    model_ref="configured-model",
                    tool_refs=[],
                    constraints={"goal_scope_only": True, "max_delegation_depth": 1},
                )
            )
            session.add(
                OrganizationModel(
                    id=org_id,
                    goal_id=goal_id,
                    strategy=strategy,
                    rationale=rationale,
                    status="ACTIVE",
                    max_agents=4,
                )
            )
            await session.flush()
            for work in [*works, recovery_work]:
                agent_id = specialist_id if work.id == recovery_work.id else coordinator_id
                session.add(
                    AssignmentModel(
                        id=uuid.uuid4(),
                        organization_id=org_id,
                        work_id=work.id,
                        agent_spec_id=agent_id,
                        role="gap-specialist" if work.id == recovery_work.id else "executor",
                        delegated_capabilities=list(
                            work.metadata_json.get("required_capabilities", caps)
                        ),
                    )
                )
            await session.flush()
            receipt = await self._receipt_in_session(session, org_id, replayed=False)
            return ReorganizationResult(
                receipt=receipt,
                recovery_work_id=recovery_work.id,
                gap_kind=gap_kind,
                method=method,
                attempt=attempt,
            )

        org.strategy = "MULTI_SPECIALIST" if method in {"COMPOSE", "BUILD"} else org.strategy
        if method == "REUSE" and org.strategy == "SINGLE_AGENT":
            org.strategy = "SINGLE_AGENT"
        org.rationale = rationale
        org.status = "ACTIVE"
        session.add(
            AssignmentModel(
                id=uuid.uuid4(),
                organization_id=org.id,
                work_id=recovery_work.id,
                agent_spec_id=specialist_id,
                role="gap-specialist",
                delegated_capabilities=caps,
            )
        )
        await session.flush()
        receipt = await self._receipt_in_session(session, org.id, replayed=False)
        return ReorganizationResult(
            receipt=receipt,
            recovery_work_id=recovery_work.id,
            gap_kind=gap_kind,
            method=method,
            attempt=attempt,
        )

    async def select_org(
        self,
        goal_id: uuid.UUID,
        *,
        weights: UtilityWeights | None = None,
    ) -> tuple[OrganizationTemplate, UtilityResult]:
        """Evaluate all candidate organizations and return the best one.

        Uses compute_utility() + select_best_organization() to drive
        organization selection based on the V3 utility function.
        """
        w = weights or DEFAULT_WEIGHTS
        templates = default_organization_space()

        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")

            works = list(
                await session.scalars(
                    select(WorkModel).where(WorkModel.goal_id == goal_id)
                )
            )
            required = sorted(
                {
                    str(cap)
                    for work in works
                    for cap in work.metadata_json.get("required_capabilities", [])
                }
            )
            verified = set(
                await session.scalars(
                    select(CapabilityModel.name).where(
                        CapabilityModel.name.in_(required),
                        CapabilityModel.status == "VERIFIED",
                        CapabilityModel.scope_goal_id.is_(None),
                    )
                )
            )

        gaps = sorted(set(required) - verified)
        agent_count_estimate = max(1, len(works))

        candidates: list[tuple[OrganizationTemplate, UtilityResult]] = []
        for tmpl in templates:
            result = compute_utility(
                tmpl,
                goal_status=goal.status,
                capability_gaps=gaps,
                agent_count=len(tmpl.roles),
                has_permit=False,
                has_human_task=False,
                estimated_cost=0.1 * len(tmpl.roles),
                estimated_latency=0.05 * len(tmpl.roles),
                weights=w,
            )
            candidates.append((tmpl, result))

        best = select_best_organization(candidates)
        if best is None:
            # Fallback to first template if no candidates
            return templates[0], compute_utility(templates[0], weights=w)
        return best

    async def organize(self, goal_id: uuid.UUID) -> OrganizationReceipt:
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            if goal.status not in {"READY", "ACTIVE"}:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "goal must be confirmed before organization",
                )
            works = list(
                await session.scalars(select(WorkModel).where(WorkModel.goal_id == goal_id))
            )
            if not works:
                raise DomainError(
                    ErrorCode.INVALID_STATE, "goal must be planned before organization"
                )
            existing = await session.scalar(
                select(OrganizationModel).where(OrganizationModel.goal_id == goal_id)
            )
            if existing is not None:
                return await self._receipt(existing.id, replayed=True)
            required = sorted(
                {
                    str(capability)
                    for work in works
                    for capability in work.metadata_json.get("required_capabilities", [])
                }
            )
            verified = set(
                await session.scalars(
                    select(CapabilityModel.name).where(
                        CapabilityModel.name.in_(required),
                        CapabilityModel.status == "VERIFIED",
                        CapabilityModel.scope_goal_id.is_(None),
                    )
                )
            )

        gaps = sorted(set(required) - verified)

        # --- V3 P1-A: utility-driven organization selection ---
        best_template, utility_result = await self.select_org(goal_id)
        strategy = best_template.strategy
        rationale = (
            f"{utility_result.rationale} | "
            f"selected template: {best_template.template_id} ({best_template.label})"
        )

        organization_id, agent_spec_id = uuid.uuid4(), uuid.uuid4()
        async with self._sessions() as session, session.begin():
            session.add(
                AgentSpecModel(
                    id=agent_spec_id,
                    name="goal-single-agent",
                    version=1,
                    status="CANDIDATE" if gaps else "ACTIVE",
                    scope_goal_id=goal_id,
                    capability_names=required,
                    model_ref="configured-model",
                    tool_refs=[],
                    constraints={"goal_scope_only": True, "max_delegation_depth": 0},
                )
            )
            for gap in gaps:
                session.add(
                    CapabilityModel(
                        id=uuid.uuid4(),
                        name=gap,
                        status="CANDIDATE",
                        scope_goal_id=goal_id,
                        description=f"Goal-scoped candidate capability for {gap}",
                        verification={"required_tests": 1, "passed_tests": 0},
                    )
                )
            session.add(
                OrganizationModel(
                    id=organization_id,
                    goal_id=goal_id,
                    strategy=strategy,
                    rationale=rationale,
                    status="ACTIVE",
                    max_agents=max(len(best_template.roles), 4),
                )
            )
            # Store utility evaluation in goal metadata
            goal_obj = await session.get(GoalModel, goal_id)
            if goal_obj is not None:
                meta = dict(goal_obj.metadata_json or {})
                meta["utility_evaluation"] = {
                    "template_id": best_template.template_id,
                    "utility": utility_result.utility,
                    "components": utility_result.components,
                    "rationale": utility_result.rationale,
                }
                goal_obj.metadata_json = meta
            await session.flush()
            for work in works:
                session.add(
                    AssignmentModel(
                        id=uuid.uuid4(),
                        organization_id=organization_id,
                        work_id=work.id,
                        agent_spec_id=agent_spec_id,
                        role="executor",
                        delegated_capabilities=list(
                            work.metadata_json.get("required_capabilities", [])
                        ),
                    )
                )
        return await self._receipt(organization_id, replayed=False)

    async def get_organization(self, goal_id: uuid.UUID) -> OrganizationReceipt:
        """Return the current organisation receipt for a Goal, or raise NOT_FOUND."""
        async with self._sessions() as session:
            org = await session.scalar(
                select(OrganizationModel).where(OrganizationModel.goal_id == goal_id)
            )
            if org is None:
                raise DomainError(
                    ErrorCode.NOT_FOUND,
                    f"no organization for goal {goal_id}; call POST /organize first",
                )
            return await self._receipt_in_session(session, org.id, replayed=True)

    async def _receipt(self, organization_id: uuid.UUID, *, replayed: bool) -> OrganizationReceipt:
        async with self._sessions() as session:
            return await self._receipt_in_session(session, organization_id, replayed=replayed)

    @staticmethod
    async def _receipt_in_session(
        session: AsyncSession,
        organization_id: uuid.UUID,
        *,
        replayed: bool,
    ) -> OrganizationReceipt:
        organization = await session.get(OrganizationModel, organization_id)
        if organization is None:
            raise RuntimeError("organization disappeared")
        assignments = list(
            await session.scalars(
                select(AssignmentModel).where(
                    AssignmentModel.organization_id == organization_id
                )
            )
        )
        agent_ids = sorted({assignment.agent_spec_id for assignment in assignments}, key=str)
        agents = list(
            await session.scalars(select(AgentSpecModel).where(AgentSpecModel.id.in_(agent_ids)))
        ) if agent_ids else []
        required = sorted({cap for agent in agents for cap in (agent.capability_names or [])})
        candidates = set(
            await session.scalars(
                select(CapabilityModel.name).where(
                    CapabilityModel.scope_goal_id == organization.goal_id,
                    CapabilityModel.status == "CANDIDATE",
                )
            )
        )
        return OrganizationReceipt(
            organization_id=organization.id,
            goal_id=organization.goal_id,
            strategy=organization.strategy,
            agent_spec_ids=agent_ids,
            required_capabilities=required,
            reused_capabilities=sorted(set(required) - candidates),
            capability_gaps=sorted(candidates),
            assignment_count=len(assignments),
            replayed=replayed,
        )

    # ------------------------------------------------------------------
    # P3-A: Adaptive organization proposal
    # ------------------------------------------------------------------

    async def propose_adaptive_organization(
        self,
        goal_id: uuid.UUID,
        *,
        weights: UtilityWeights | None = None,
        actor: str = "regent-core",
    ) -> dict[str, Any]:
        """Propose an adaptive organization based on utility evaluation.

        Evaluates all candidate templates and returns a proposal dict
        with the best organization, utility scores, and rationale.
        """
        best_template, utility_result = await self.select_org(goal_id, weights=weights)

        proposal = {
            "goal_id": str(goal_id),
            "proposed_template": best_template.template_id,
            "proposed_label": best_template.label,
            "proposed_strategy": best_template.strategy,
            "proposed_roles": [
                {
                    "role": r.role,
                    "capabilities": r.capability_requirements,
                    "constraints": r.constraints,
                }
                for r in best_template.roles
            ],
            "utility": utility_result.utility,
            "utility_components": utility_result.components,
            "utility_rationale": utility_result.rationale,
            "proposed_by": actor,
        }
        return proposal
