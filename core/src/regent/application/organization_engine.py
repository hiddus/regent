"""AAR-1 Organization Engine — certified templates, C/V/R, utility, version activation.

HeuristicUtilityV1 only; not calibrated success probability / global optimum.
Adaptive free-form topology is ROLLOUT_NOT_ALLOWED.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.policy_engine import (
    PolicyEngine,
    PolicyEvaluationRequest,
    PolicyOutcome,
    PolicyRule,
    canonical_hash,
    default_system_rules,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import (
    OrganizationCandidateCheckModel,
    OrganizationCandidateRecordModel,
    OrganizationDecisionModel,
    OrganizationSnapshotModel,
    OrganizationTemplateModel,
    OrganizationVersionModel,
)
from regent.infrastructure.models import OrganizationModel

UTILITY_POLICY_VERSION = "HeuristicUtilityV1"
GENERATOR_VERSION = "org-engine/v1"
DEFAULT_EPSILON = 0.05

CheckResult = Literal["PASS", "FAIL", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class UtilityWeightsV1:
    success: float = 0.30
    cost: float = 0.20
    latency: float = 0.15
    human_burden: float = 0.15
    residual_operational_risk: float = 0.15
    explainability: float = 0.05

    def validate(self) -> None:
        total = (
            self.success
            + self.cost
            + self.latency
            + self.human_burden
            + self.residual_operational_risk
            + self.explainability
        )
        if abs(total - 1.0) > 1e-9 or min(
            self.success,
            self.cost,
            self.latency,
            self.human_burden,
            self.residual_operational_risk,
            self.explainability,
        ) < 0:
            raise ValueError("utility weights must be non-negative and sum to 1")

    def as_dict(self) -> dict[str, float]:
        return {
            "success": self.success,
            "cost": self.cost,
            "latency": self.latency,
            "human_burden": self.human_burden,
            "residual_operational_risk": self.residual_operational_risk,
            "explainability": self.explainability,
        }


@dataclass(frozen=True, slots=True)
class PredictedUtility:
    value: float
    components: dict[str, float]
    weights: dict[str, float]
    policy_version: str
    interval: tuple[float, float]
    missing_value_policy: str
    rationale: str


@dataclass
class FeasibilityReport:
    candidate_id: str
    c: CheckResult
    v: CheckResult
    r: CheckResult
    reason_codes: list[str] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        # UNKNOWN is treated as not feasible (hard filter).
        return (
            self.c == "PASS"
            and self.v == "PASS"
            and self.r == "PASS"
        )


@dataclass
class OrganizationDecisionBundle:
    decision_id: uuid.UUID
    selected_candidate_id: uuid.UUID | None
    feasible_count: int
    infeasible_count: int
    predicted_utility: float | None
    decision_json: dict[str, Any]
    status: str
    infeasibility_report: dict[str, Any] | None = None


def compute_heuristic_utility_v1(
    topology: dict[str, Any],
    *,
    capability_gaps: list[str] | None = None,
    weights: UtilityWeightsV1 | None = None,
    uncertainty_penalty: float = 0.02,
) -> PredictedUtility:
    """Named HeuristicUtilityV1 — not calibrated P(success)."""
    w = weights or UtilityWeightsV1()
    w.validate()
    gaps = capability_gaps or []
    roles = list(topology.get("roles") or [])
    agent_count = max(1, len(roles))
    strategy = str(topology.get("strategy") or "SINGLE_AGENT")

    gap_ratio = len(gaps) / max(agent_count + len(gaps), 1)
    success = 1.0 - 0.5 * gap_ratio
    if strategy == "FIXED_TEMPLATE" and agent_count >= 3:
        success = min(1.0, success + 0.10)

    cost = max(0.0, 1.0 - 0.1 * agent_count)
    if strategy == "SINGLE_AGENT":
        cost = min(1.0, cost + 0.15)

    latency = max(0.0, 1.0 - 0.05 * agent_count)
    if strategy == "SINGLE_AGENT":
        latency = min(1.0, latency + 0.10)

    human_burden = 1.0
    residual_risk = max(0.0, 1.0 - 0.15 * len(gaps))
    explainability = 1.0 if strategy == "SINGLE_AGENT" else max(0.0, 1.0 - 0.15 * (agent_count - 1))

    components = {
        "success": round(success, 4),
        "cost": round(cost, 4),
        "latency": round(latency, 4),
        "human_burden": round(human_burden, 4),
        "residual_operational_risk": round(residual_risk, 4),
        "explainability": round(explainability, 4),
    }
    raw = sum(w.as_dict()[k] * components[k] for k in components)
    value = round(raw - uncertainty_penalty, 4)
    return PredictedUtility(
        value=value,
        components=components,
        weights=w.as_dict(),
        policy_version=UTILITY_POLICY_VERSION,
        interval=(round(max(0.0, value - 0.05), 4), round(min(1.0, value + 0.05), 4)),
        missing_value_policy="treat_as_zero_component",
        rationale=(
            f"{UTILITY_POLICY_VERSION} U={value} agents={agent_count} "
            f"gaps={len(gaps)} (not calibrated success probability)"
        ),
    )


def feasibility_cvr(
    topology: dict[str, Any],
    *,
    available_capabilities: set[str] | None = None,
    budget_remaining: float | None = None,
    required_budget: float = 0.0,
    policy_outcome: PolicyOutcome | None = None,
    unknown_resource: bool = False,
) -> FeasibilityReport:
    """Hard filter: UNKNOWN treated as FAIL for admission."""
    reasons: list[str] = []
    available = available_capabilities or set()

    # C — capability / constitution / compliance hard constraints
    # available_capabilities must be concrete names (never treat "*" as wildcard).
    required_caps: set[str] = set()
    for role in topology.get("roles") or []:
        for cap in role.get("capabilities") or role.get("capability_requirements") or []:
            required_caps.add(str(cap))
    concrete = {c for c in available if c and c != "*"}
    missing = sorted(required_caps - concrete)
    if missing:
        c: CheckResult = "FAIL"
        reasons.append(f"CAPABILITY_GAP:{','.join(missing)}")
    elif policy_outcome is PolicyOutcome.DENY:
        c = "FAIL"
        reasons.append("POLICY_DENIED")
    else:
        c = "PASS"

    # V — validity / topology invariants
    roles = list(topology.get("roles") or [])
    if not roles:
        v: CheckResult = "FAIL"
        reasons.append("EMPTY_TOPOLOGY")
    elif topology.get("template_id") is None and topology.get("generation_method") == "LLM_DRAFT":
        v = "FAIL"
        reasons.append("UNCERTIFIED_TEMPLATE")
    else:
        invariants = set(topology.get("invariants") or [])
        if "producer_reviewer_separation" in invariants:
            producers = {r["role"] for r in roles if r.get("role") in {"dev", "executor"}}
            reviewers = {
                r["role"]
                for r in roles
                if r.get("role") in {"qa", "reviewer"} or r.get("independent_reviewer")
            }
            if producers & reviewers:
                v = "FAIL"
                reasons.append("PRODUCER_REVIEWER_COLLISION")
            elif not reviewers:
                v = "FAIL"
                reasons.append("MISSING_INDEPENDENT_REVIEWER")
            else:
                v = "PASS"
        else:
            v = "PASS"

    # R — resources
    if unknown_resource:
        r: CheckResult = "UNKNOWN"
        reasons.append("RESOURCE_UNKNOWN")
    elif budget_remaining is not None and required_budget > budget_remaining:
        r = "FAIL"
        reasons.append("BUDGET_EXCEEDED")
    else:
        r = "PASS"

    # UNKNOWN == FAIL for feasibility set membership
    return FeasibilityReport(
        candidate_id=str(topology.get("template_id") or "unknown"),
        c=c,
        v=v,
        r=r,
        reason_codes=reasons
        + (["UNKNOWN_TREATED_AS_FAIL"] if unknown_resource else []),
    )


def select_feasible_argmax(
    scored: list[tuple[str, PredictedUtility, FeasibilityReport, dict[str, Any]]],
) -> tuple[str, PredictedUtility, dict[str, Any]] | None:
    """Argmax over F_t only; tie-break: lower cost → fewer agents → template id."""
    feasible = [(cid, util, top) for cid, util, report, top in scored if report.feasible]
    if not feasible:
        return None

    def sort_key(item: tuple[str, PredictedUtility, dict[str, Any]]) -> tuple:
        cid, util, top = item
        agent_count = len(top.get("roles") or [])
        pred_cost = 1.0 - util.components.get("cost", 0.0)
        return (-util.value, pred_cost, agent_count, cid)

    feasible.sort(key=sort_key)
    best = feasible[0]
    return best[0], best[1], best[2]


class OrganizationEngine:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        policy: PolicyEngine | None = None,
        enforce_cvr: bool = True,
    ) -> None:
        self._sessions = sessions
        self._policy = policy or PolicyEngine(sessions)
        self._enforce_cvr = enforce_cvr

    async def list_certified_templates(
        self, session: AsyncSession
    ) -> list[OrganizationTemplateModel]:
        rows = await session.scalars(
            select(OrganizationTemplateModel).where(
                OrganizationTemplateModel.status == "CERTIFIED"
            )
        )
        return list(rows)

    def evaluate_candidates(
        self,
        templates: list[dict[str, Any]],
        *,
        available_capabilities: set[str] | None = None,
        budget_remaining: float | None = None,
        rules: list[PolicyRule] | None = None,
        preferred_template_id: str | None = None,
    ) -> OrganizationDecisionBundle:
        """Pure decision pipeline used by dual-write shadow and enforce paths.

        When ``preferred_template_id`` is feasible it wins over utility argmax
        (used for opt-in certified hive; does not invent free-form topologies).
        """
        rules = rules or default_system_rules()
        scored: list[tuple[str, PredictedUtility, FeasibilityReport, dict[str, Any]]] = []
        candidate_reports: list[dict[str, Any]] = []

        for tmpl in templates:
            topology = dict(tmpl.get("topology_json") or tmpl)
            template_name = str(
                topology.get("template_id") or tmpl.get("name") or uuid.uuid4()
            )
            policy_result = self._policy.evaluate(
                PolicyEvaluationRequest(
                    decision_point="ORG_CANDIDATE_ADMISSION",
                    subject_type="ORG",
                    subject_id=template_name,
                    action="admit_candidate",
                    resource={"template_id": template_name},
                    input_snapshot={"topology": topology},
                    rules=rules,
                    correlation_id=template_name,
                )
            )
            report = feasibility_cvr(
                topology,
                available_capabilities=available_capabilities,
                budget_remaining=budget_remaining,
                policy_outcome=policy_result.outcome,
            )
            if not self._enforce_cvr:
                # Shadow may still compute but admission is advisory.
                pass
            util = compute_heuristic_utility_v1(
                topology,
                capability_gaps=[
                    c.split(":", 1)[1]
                    for c in report.reason_codes
                    if c.startswith("CAPABILITY_GAP:")
                ]
                or None,
            )
            # High utility but failed C/V/R must not enter F_t
            scored.append((template_name, util, report, topology))
            candidate_reports.append(
                {
                    "template_id": template_name,
                    "c": report.c,
                    "v": report.v,
                    "r": report.r,
                    "feasible": report.feasible,
                    "predicted_utility": util.value,
                    "utility_components": util.components,
                    "utility_interval": list(util.interval),
                    "reason_codes": report.reason_codes,
                    "policy_outcome": policy_result.outcome.value,
                }
            )

        selected = select_feasible_argmax(scored)
        if preferred_template_id:
            for cid, util, report, top in scored:
                if cid == preferred_template_id and report.feasible:
                    selected = (cid, util, top)
                    break
        decision_id = uuid.uuid4()
        if selected is None:
            return OrganizationDecisionBundle(
                decision_id=decision_id,
                selected_candidate_id=None,
                feasible_count=0,
                infeasible_count=len(scored),
                predicted_utility=None,
                status="REJECTED",
                decision_json={
                    "utility_policy_version": UTILITY_POLICY_VERSION,
                    "candidates": candidate_reports,
                    "selected": None,
                    "error": "NO_FEASIBLE_ORGANIZATION",
                },
                infeasibility_report={
                    "code": "NO_FEASIBLE_ORGANIZATION",
                    "candidates": candidate_reports,
                },
            )

        sel_id, sel_util, sel_top = selected
        feasible_n = sum(1 for _, __, rep, ___ in scored if rep.feasible)
        return OrganizationDecisionBundle(
            decision_id=decision_id,
            # Must be decision-scoped: template-only uuid5 collides across goals.
            selected_candidate_id=uuid.uuid5(
                uuid.NAMESPACE_URL, f"{decision_id}:{sel_id}"
            ),
            feasible_count=feasible_n,
            infeasible_count=len(scored) - feasible_n,
            predicted_utility=sel_util.value,
            status="ACCEPTED",
            decision_json={
                "utility_policy_version": UTILITY_POLICY_VERSION,
                "candidates": candidate_reports,
                "selected": {
                    "template_id": sel_id,
                    "predicted_utility": sel_util.value,
                    "components": sel_util.components,
                    "interval": list(sel_util.interval),
                    "rationale": sel_util.rationale,
                    "topology": sel_top,
                },
                "tie_break": "lower_cost_then_fewer_agents_then_template_id",
                "preferred_template_id": preferred_template_id,
            },
        )

    async def decide_and_persist(
        self,
        session: AsyncSession,
        *,
        goal_id: uuid.UUID,
        organization_id: uuid.UUID,
        trigger: str = "INITIAL",
        actor: str = "regent-core",
        available_capabilities: set[str] | None = None,
        previous_version_id: uuid.UUID | None = None,
        constitution_version_id: uuid.UUID | None = None,
        activate: bool = True,
        version_id: uuid.UUID | None = None,
        preferred_template_id: str | None = None,
    ) -> OrganizationDecisionBundle:
        templates = await self.list_certified_templates(session)
        tmpl_payloads = [
            {
                "name": t.name,
                "topology_json": {
                    **dict(t.topology_json),
                    "template_id": t.topology_json.get("template_id") or t.name,
                },
            }
            for t in templates
        ]
        if not tmpl_payloads:
            # Champion fallback when templates table empty (dev / pre-seed).
            tmpl_payloads = [
                {
                    "name": "single-agent-v1",
                    "topology_json": {
                        "template_id": "single-agent-v1",
                        "strategy": "SINGLE_AGENT",
                        "roles": [{"role": "executor", "capabilities": []}],
                    },
                }
            ]
        # Default champion is single-agent via utility; preferred_template_id
        # (certified hive opt-in) overrides when that candidate is feasible.
        bundle = self.evaluate_candidates(
            tmpl_payloads,
            available_capabilities=available_capabilities,
            preferred_template_id=preferred_template_id,
        )
        # Fix feasible_count accurately
        feas = sum(1 for c in bundle.decision_json["candidates"] if c["feasible"])
        bundle.feasible_count = feas
        bundle.infeasible_count = len(bundle.decision_json["candidates"]) - feas

        resource_snap = OrganizationSnapshotModel(
            id=uuid.uuid4(),
            goal_id=goal_id,
            snapshot_type="RESOURCE",
            content_json={"available_capabilities": sorted(available_capabilities or [])},
            content_hash=canonical_hash(
                {"available_capabilities": sorted(available_capabilities or [])}
            ),
        )
        state_snap = OrganizationSnapshotModel(
            id=uuid.uuid4(),
            goal_id=goal_id,
            snapshot_type="STATE",
            content_json={"trigger": trigger},
            content_hash=canonical_hash({"trigger": trigger}),
        )
        session.add(resource_snap)
        session.add(state_snap)
        await session.flush()

        decision = OrganizationDecisionModel(
            id=bundle.decision_id,
            goal_id=goal_id,
            previous_organization_version_id=previous_version_id,
            constitution_version_id=constitution_version_id,
            resource_snapshot_id=resource_snap.id,
            state_snapshot_id=state_snap.id,
            utility_policy_version=UTILITY_POLICY_VERSION,
            selected_candidate_id=bundle.selected_candidate_id,
            trigger=trigger,
            status=bundle.status,
            decision_json=bundle.decision_json,
            created_by=actor,
        )
        session.add(decision)
        await session.flush()

        template_by_name = {t.name: t for t in templates}
        check_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
        for cand in bundle.decision_json["candidates"]:
            name = cand["template_id"]
            tmpl = template_by_name.get(name)
            topology = (tmpl.topology_json if tmpl else {}) or {}
            cand_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{bundle.decision_id}:{name}")
            status = (
                "SELECTED"
                if bundle.decision_json.get("selected")
                and bundle.decision_json["selected"]["template_id"] == name
                else ("FEASIBLE" if cand["feasible"] else "INFEASIBLE")
            )
            session.add(
                OrganizationCandidateRecordModel(
                    id=cand_id,
                    decision_id=bundle.decision_id,
                    template_id=tmpl.id if tmpl else None,
                    topology_json=dict(topology),
                    required_resources_json={},
                    status=status,
                    generation_method="CERTIFIED_TEMPLATE",
                    generator_version=GENERATOR_VERSION,
                    predicted_utility=cand["predicted_utility"],
                    utility_components_json=cand["utility_components"],
                )
            )
            check_rows.append((cand_id, cand))

        # Flush candidates before checks to satisfy FK (autoflush order is not reliable).
        await session.flush()
        for cand_id, cand in check_rows:
            snap_hash = canonical_hash(cand)
            for check_type, result in (("C", cand["c"]), ("V", cand["v"]), ("R", cand["r"])):
                session.add(
                    OrganizationCandidateCheckModel(
                        id=uuid.uuid4(),
                        candidate_id=cand_id,
                        check_type=check_type,
                        result=result,
                        reason_codes=list(cand.get("reason_codes") or []),
                        evidence_refs=[],
                        snapshot_hash=snap_hash,
                    )
                )

        if bundle.status != "ACCEPTED" or not activate:
            if bundle.status != "ACCEPTED":
                raise DomainError(
                    ErrorCode.NO_FEASIBLE_ORGANIZATION,
                    "no feasible organization after C/V/R",
                )
            return bundle

        await self.activate_version(
            session,
            organization_id=organization_id,
            decision_id=bundle.decision_id,
            topology=bundle.decision_json["selected"]["topology"],
            predecessor_id=previous_version_id,
            version_id=version_id,
        )
        return bundle

    async def activate_version(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        decision_id: uuid.UUID,
        topology: dict[str, Any],
        predecessor_id: uuid.UUID | None = None,
        version_id: uuid.UUID | None = None,
    ) -> OrganizationVersionModel:
        org = await session.get(OrganizationModel, organization_id, with_for_update=True)
        if org is None:
            raise DomainError(ErrorCode.NOT_FOUND, "organization not found")

        # Policy gate for activation
        policy_result = self._policy.evaluate(
            PolicyEvaluationRequest(
                decision_point="ORG_ACTIVATION",
                subject_type="ORG",
                subject_id=str(organization_id),
                action="activate_organization",
                resource={"organization_id": str(organization_id)},
                input_snapshot={"topology": topology, "decision_id": str(decision_id)},
                rules=default_system_rules(),
                correlation_id=str(decision_id),
            )
        )
        if self._enforce_cvr and policy_result.outcome is PolicyOutcome.DENY:
            raise DomainError(ErrorCode.POLICY_DENIED, "org activation denied")

        if org.current_version_id is not None:
            current = await session.get(
                OrganizationVersionModel, org.current_version_id, with_for_update=True
            )
            if current is not None and current.status == "ACTIVE":
                current.status = "SUPERSEDED"
                current.retired_at = datetime.now(UTC)
                next_version = current.version + 1
                pred = current.id
            elif (
                current is None
                and version_id is not None
                and org.current_version_id == version_id
            ):
                # Contract: org row pre-allocated version_id before Version row exists.
                next_version = 1
                pred = predecessor_id
            else:
                next_version = 1
                pred = predecessor_id
        else:
            next_version = 1
            pred = predecessor_id

        new_id = version_id or uuid.uuid4()
        version = OrganizationVersionModel(
            id=new_id,
            organization_id=organization_id,
            version=next_version,
            predecessor_id=pred,
            decision_id=decision_id,
            topology_json=dict(topology),
            status="ACTIVE",
            activated_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
        org.current_version_id = version.id
        return version

    async def rollback(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        target_version_id: uuid.UUID,
        actor: str = "owner",
    ) -> OrganizationVersionModel:
        """Rollback creates a NEW version (never mutates history)."""
        org = await session.get(OrganizationModel, organization_id, with_for_update=True)
        if org is None:
            raise DomainError(ErrorCode.NOT_FOUND, "organization not found")
        target = await session.get(OrganizationVersionModel, target_version_id)
        if target is None or target.organization_id != organization_id:
            raise DomainError(ErrorCode.STALE_ORGANIZATION_VERSION, "target version not found")

        decision = OrganizationDecisionModel(
            id=uuid.uuid4(),
            goal_id=org.goal_id,
            previous_organization_version_id=org.current_version_id,
            utility_policy_version=UTILITY_POLICY_VERSION,
            selected_candidate_id=None,
            trigger="ROLLBACK",
            status="ACCEPTED",
            decision_json={
                "rollback_to": str(target_version_id),
                "topology": target.topology_json,
                "actor": actor,
            },
            created_by=actor,
        )
        session.add(decision)
        await session.flush()
        return await self.activate_version(
            session,
            organization_id=organization_id,
            decision_id=decision.id,
            topology=dict(target.topology_json),
            predecessor_id=org.current_version_id,
        )


def shadow_compare(
    legacy_strategy: str,
    engine_bundle: OrganizationDecisionBundle,
) -> dict[str, Any]:
    """M2 shadow: explain any divergence; never changes production selection."""
    selected = (engine_bundle.decision_json.get("selected") or {}).get("template_id")
    legacy_template = (
        "single-agent-v1"
        if legacy_strategy in {"SINGLE_AGENT", "single-agent-v1"}
        else legacy_strategy
    )
    match = selected == legacy_template or (
        legacy_strategy == "SINGLE_AGENT" and selected == "single-agent-v1"
    )
    return {
        "match": match,
        "legacy_strategy": legacy_strategy,
        "engine_selected": selected,
        "engine_status": engine_bundle.status,
        "explanation": (
            "aligned"
            if match
            else (
                "engine_rejected_all"
                if engine_bundle.status != "ACCEPTED"
                else "template_divergence_explained_by_cvr_or_utility"
            )
        ),
        "infeasibility": engine_bundle.infeasibility_report,
    }


def topology_content_hash(topology: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(topology, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
