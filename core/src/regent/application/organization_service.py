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
        description="默认最小组织: 单一 Agent 负责规划、生成、验证全部工作。",
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
            "固定三角色模板: PM 负责需求评审与验收, Dev 负责产品构建, "
            "QA 负责交付审查与证据采集. 适合 LARGE 目标."
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
    # AAR-1 M3 Read-switch fields (optional until dual-write/backfill)
    organization_version_id: uuid.UUID | None = None
    decision_id: uuid.UUID | None = None
    constitution_version_id: uuid.UUID | None = None
    shadow_compare: dict[str, Any] | None = None


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
            version_id = uuid.uuid4()
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
                    current_version_id=version_id,
                )
            )
            await self._write_contract_projection_version(
                session,
                organization_id=org_id,
                version_id=version_id,
                strategy=strategy,
                rationale=rationale,
                version_number=1,
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
        await self._append_reorg_version(
            session,
            organization=org,
            strategy=org.strategy,
            rationale=rationale,
        )
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
            if goal.status not in {"DRAFT", "READY", "ACTIVE"}:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "goal is not open for organization",
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

        from regent.application.aar1_contract import (
            engine_is_primary_writer,
            legacy_org_writes_allowed,
        )
        from regent.config import get_settings

        phase = get_settings().aar1_phase
        if engine_is_primary_writer(phase):
            return await self._organize_contract(goal_id, works=works, gaps=gaps)

        # --- Pre-Contract: legacy utility selection + dual-write Version ---
        if not legacy_org_writes_allowed(phase):
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"unexpected aar1_phase={phase} for legacy organize path",
            )

        best_template, utility_result = await self.select_org(goal_id)
        strategy = best_template.strategy
        rationale = (
            f"{utility_result.rationale} | "
            f"selected template: {best_template.template_id} ({best_template.label})"
        )

        organization_id, agent_spec_id = uuid.uuid4(), uuid.uuid4()
        version_id = uuid.uuid4()
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
                    current_version_id=version_id,
                )
            )
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
            aar1_meta = await self._dual_write_organization(
                session,
                organization_id=organization_id,
                goal_id=goal_id,
                strategy=strategy,
                best_template_id=best_template.template_id,
                utility=utility_result.utility,
                gaps=gaps,
                version_id=version_id,
            )
            if goal_obj is not None and aar1_meta:
                meta = dict(goal_obj.metadata_json or {})
                meta["aar1"] = aar1_meta
                goal_obj.metadata_json = meta
        return await self._receipt(organization_id, replayed=False)

    async def _organize_contract(
        self,
        goal_id: uuid.UUID,
        *,
        works: list[Any],
        gaps: list[str],
    ) -> OrganizationReceipt:
        """M5 Contract: OrganizationEngine is the sole topology writer.

        Legacy mutable strategy/rationale are projections of the active Version only.
        Dual-write / fail-open legacy selection is not used.

        When ``REGENT_AAR1_CERTIFIED_HIVE=true`` and capabilities admit
        ``pm-dev-independent-qa-v1``, that certified fixed template is preferred.
        Adaptive free-form topology remains ROLLOUT_NOT_ALLOWED.
        """
        from regent.application.aar1_contract import (
            CERTIFIED_HIVE_TEMPLATE_ID,
            certified_hive_preferred,
            is_certified_hive_topology,
        )
        from regent.application.hive_runtime import materialize_hive_topology
        from regent.application.organization_engine import OrganizationEngine
        from regent.config import get_settings

        settings = get_settings()
        preferred = certified_hive_preferred(enabled=settings.aar1_certified_hive)

        organization_id = uuid.uuid4()
        version_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            available = await self._resolve_available_capabilities(session)
            # Goal work gaps are not platform-available; keep them out of C/V/R set.
            available -= set(gaps)

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
                    strategy="PENDING_CONTRACT",
                    rationale="pending OrganizationEngine decision",
                    status="ACTIVE",
                    max_agents=4,
                    current_version_id=version_id,
                )
            )
            # current_version_id FK is DEFERRABLE INITIALLY DEFERRED (0034), so the
            # Organization row may flush before OrganizationVersion exists.
            await session.flush()

            engine = OrganizationEngine(self._sessions, enforce_cvr=True)
            bundle = await engine.decide_and_persist(
                session,
                goal_id=goal_id,
                organization_id=organization_id,
                trigger="INITIAL",
                available_capabilities=available,
                preferred_template_id=preferred,
                activate=True,
                version_id=version_id,
            )

            org = await session.get(OrganizationModel, organization_id)
            selected = bundle.decision_json.get("selected") or {}
            topology = dict(selected.get("topology") or {})
            template_id = str(selected.get("template_id") or "single-agent-v1")
            strategy = str(topology.get("strategy") or "SINGLE_AGENT")
            if org is None or org.current_version_id is None:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "contract organize did not set current_version_id",
                )
            # Projection only — Version remains the immutable truth source.
            org.strategy = strategy
            org.rationale = (
                f"contract:{template_id} | "
                f"decision={bundle.decision_id} | "
                f"utility={bundle.predicted_utility}"
            )
            org.max_agents = max(len(topology.get("roles") or []), 4)

            if is_certified_hive_topology(topology) or template_id == CERTIFIED_HIVE_TEMPLATE_ID:
                await materialize_hive_topology(
                    session,
                    goal_id=goal_id,
                    organization_id=organization_id,
                    organization_version_id=org.current_version_id,
                    topology=topology,
                    works=works,
                    gaps=gaps,
                )
            else:
                agent_spec_id = uuid.uuid4()
                caps = sorted(
                    {
                        str(capability)
                        for work in works
                        for capability in work.metadata_json.get("required_capabilities", [])
                    }
                )
                session.add(
                    AgentSpecModel(
                        id=agent_spec_id,
                        name="goal-single-agent",
                        version=1,
                        status="CANDIDATE" if gaps else "ACTIVE",
                        scope_goal_id=goal_id,
                        capability_names=caps,
                        model_ref="configured-model",
                        tool_refs=[],
                        constraints={"goal_scope_only": True, "max_delegation_depth": 0},
                    )
                )
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

            goal_obj = await session.get(GoalModel, goal_id)
            if goal_obj is not None:
                meta = dict(goal_obj.metadata_json or {})
                meta["utility_evaluation"] = {
                    "template_id": template_id,
                    "utility": bundle.predicted_utility,
                    "components": (selected.get("components") or {}),
                    "rationale": selected.get("rationale") or org.rationale,
                    "writer": "organization_engine",
                    "phase": "contract",
                }
                meta["aar1"] = {
                    "organization_version_id": str(org.current_version_id),
                    "decision_id": str(bundle.decision_id),
                    "phase": "contract",
                    "engine_selected": template_id,
                    "legacy_dual_write": False,
                    "certified_hive_opt_in": bool(preferred),
                    "available_capabilities": sorted(available),
                }
                goal_obj.metadata_json = meta

        return await self._receipt(organization_id, replayed=False)

    @staticmethod
    async def _resolve_available_capabilities(session: AsyncSession) -> set[str]:
        """Concrete VERIFIED capability names for C/V/R (never wildcard '*')."""
        rows = await session.scalars(
            select(CapabilityModel.name).where(
                CapabilityModel.status == "VERIFIED",
                CapabilityModel.scope_goal_id.is_(None),
            )
        )
        return {str(name) for name in rows}

    @staticmethod
    async def _write_contract_projection_version(
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        version_id: uuid.UUID,
        strategy: str,
        rationale: str,
        version_number: int = 1,
        predecessor_id: uuid.UUID | None = None,
    ) -> None:
        """Persist an OrganizationVersion that strategy/rationale project from."""
        from datetime import UTC, datetime

        from regent.infrastructure.aar1_models import OrganizationVersionModel

        session.add(
            OrganizationVersionModel(
                id=version_id,
                organization_id=organization_id,
                version=version_number,
                predecessor_id=predecessor_id,
                decision_id=None,
                topology_json={
                    "template_id": (
                        "single-agent-v1"
                        if strategy in {"SINGLE_AGENT", "single-agent-v1"}
                        else strategy
                    ),
                    "strategy": strategy,
                    "roles": [{"role": "executor", "capabilities": []}],
                    "rationale": rationale,
                    "projection": True,
                },
                status="ACTIVE",
                activated_at=datetime.now(UTC),
            )
        )

    async def _append_reorg_version(
        self,
        session: AsyncSession,
        *,
        organization: OrganizationModel,
        strategy: str,
        rationale: str,
    ) -> None:
        """Contract: do not mutate org topology without a new Version row."""
        from datetime import UTC, datetime

        from regent.infrastructure.aar1_models import OrganizationVersionModel

        pred = organization.current_version_id
        next_version = 1
        if pred is not None:
            current = await session.get(OrganizationVersionModel, pred)
            if current is not None and current.status == "ACTIVE":
                current.status = "SUPERSEDED"
                current.retired_at = datetime.now(UTC)
                next_version = current.version + 1
        new_id = uuid.uuid4()
        await self._write_contract_projection_version(
            session,
            organization_id=organization.id,
            version_id=new_id,
            strategy=strategy,
            rationale=rationale,
            version_number=next_version,
            predecessor_id=pred,
        )
        organization.current_version_id = new_id

    async def _dual_write_organization(
        self,
        session: Any,
        *,
        organization_id: uuid.UUID,
        goal_id: uuid.UUID,
        strategy: str,
        best_template_id: str,
        utility: float,
        gaps: list[str],
        version_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        """Pre-Contract dual-write — never blocks legacy organize on shadow divergence.

        Forbidden in ``contract`` phase (caller must use ``_organize_contract``).
        """
        from regent.application.aar1_contract import is_contract_phase
        from regent.config import get_settings

        settings = get_settings()
        if is_contract_phase(settings.aar1_phase):
            raise DomainError(
                ErrorCode.INVALID_STATE,
                "legacy dual-write forbidden in contract phase",
            )
        try:
            from regent.application.organization_engine import (
                OrganizationEngine,
                compute_heuristic_utility_v1,
                shadow_compare,
            )
            from regent.infrastructure.aar1_models import OrganizationVersionModel

            phase = settings.aar1_phase
            engine = OrganizationEngine(
                self._sessions,
                enforce_cvr=phase in {"read_switch", "enforce"},
            )
            certified = await engine.list_certified_templates(session)
            payloads = [
                {
                    "name": t.name,
                    "topology_json": {
                        **dict(t.topology_json),
                        "template_id": t.topology_json.get("template_id") or t.name,
                    },
                }
                for t in certified
            ]
            if not payloads:
                payloads = [
                    {
                        "name": "single-agent-v1",
                        "topology_json": {
                            "template_id": "single-agent-v1",
                            "strategy": "SINGLE_AGENT",
                            "roles": [{"role": "executor", "capabilities": []}],
                        },
                    }
                ]
            available = await self._resolve_available_capabilities(session)
            available -= set(gaps)
            bundle = engine.evaluate_candidates(
                payloads, available_capabilities=available
            )

            compare = shadow_compare(strategy, bundle)
            topology = {
                "template_id": best_template_id,
                "strategy": strategy,
                "roles": [{"role": "executor", "capabilities": []}],
                "legacy_utility": utility,
                "heuristic": compute_heuristic_utility_v1(
                    {"strategy": strategy, "roles": [{"role": "executor"}]}
                ).rationale,
            }
            vid = version_id or uuid.uuid4()
            version = OrganizationVersionModel(
                id=vid,
                organization_id=organization_id,
                version=1,
                predecessor_id=None,
                decision_id=bundle.decision_id if phase != "expand" else None,
                topology_json=topology,
                status="ACTIVE",
                activated_at=__import__("datetime").datetime.now(
                    __import__("datetime").UTC
                ),
            )
            session.add(version)
            org = await session.get(OrganizationModel, organization_id)
            if org is not None:
                org.current_version_id = version.id

            return {
                "organization_version_id": str(version.id),
                "decision_id": str(bundle.decision_id),
                "shadow": compare,
                "phase": phase,
                "engine_selected": (bundle.decision_json.get("selected") or {}).get(
                    "template_id"
                ),
            }
        except Exception as exc:
            return {
                "dual_write_error": str(exc),
                "phase": "failed_open_legacy",
            }

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
        meta = await _goal_aar1_meta(session, organization.goal_id)
        decision_raw = (meta or {}).get("decision_id")
        decision_id = uuid.UUID(decision_raw) if decision_raw else None
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
            organization_version_id=organization.current_version_id,
            decision_id=decision_id,
            constitution_version_id=None,
            shadow_compare=(meta or {}).get("shadow") if meta else None,
        )

    # ------------------------------------------------------------------
    # P3-A: Adaptive organization proposal (proposal only — ROLLOUT gated)
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
        Does NOT activate adaptive multi-agent as default (ROLLOUT_NOT_ALLOWED).
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
            "rollout_gate": "ROLLOUT_NOT_ALLOWED",
        }
        # MA-6: attach P2-5 gate skeleton; never activate without GO DecisionRecord.
        from regent.application.p25_adaptive_gate import enrich_adaptive_proposal_skeleton

        return enrich_adaptive_proposal_skeleton(proposal, decision_record=None)


async def _goal_aar1_meta(session: AsyncSession, goal_id: uuid.UUID) -> dict[str, Any] | None:
    goal = await session.get(GoalModel, goal_id)
    if goal is None:
        return None
    aar1 = (goal.metadata_json or {}).get("aar1")
    return dict(aar1) if isinstance(aar1, dict) else None
