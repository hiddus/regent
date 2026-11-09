"""Organization routing TaskFeatures and candidate scoring bias (PRD §10.1 / Spec §18.1).

Exploration / OrganizationSpace keeps multi-agent candidates admitted (sandbox may
run). Frozen rules may demote multi-agent topologies for *production-default
recommendation* only — they never hard-exclude them from the candidate space.
Statistical Gate (P2-4) remains the authority for production rollout enablement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

TASK_FEATURES_VERSION = "task-features/v1"
PRUNE_POLICY_VERSION = "org-space-prune/v2"
SINGLE_AGENT_BASELINE_THRESHOLD = 0.45

# Production-default recommendation demotion penalties (not exploration bans).
DEMOTION_PENALTY_R1_HIGH_BASELINE = 0.25
DEMOTION_PENALTY_R2_STRONG_SEQUENTIAL = 0.20
DEMOTION_PENALTY_R3 = 0.15
DEMOTION_PENALTY_R0_DEFAULT = 0.10


class TaskFeatures(BaseModel):
    tool_call_density: float = Field(ge=0.0)
    decomposability_score: float = Field(ge=0.0, le=1.0)
    sequential_dependency_score: float = Field(ge=0.0, le=1.0)
    single_agent_baseline_success_rate: float = Field(ge=0.0, le=1.0)
    independent_verification_required: bool
    estimated_parallelism_ceiling: float = Field(ge=0.0, le=1.0)
    features_version: str = TASK_FEATURES_VERSION

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class PruneHit:
    rule_id: str
    reason: str
    effect: str = "demote"  # "demote" | "warn"
    demoted_template_ids: list[str] = field(default_factory=list)
    # Legacy alias kept for SchedulingDecision consumers; demotions do not exclude.
    excluded_template_ids: list[str] = field(default_factory=list)
    demotion_penalty: float = 0.0


@dataclass(frozen=True, slots=True)
class OrganizationSpacePruneResult:
    admitted: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    demoted: list[dict[str, Any]]
    hits: list[PruneHit]
    features: TaskFeatures
    demotion_penalties: dict[str, float] = field(default_factory=dict)
    production_default_recommended_template_ids: list[str] = field(default_factory=list)
    policy_version: str = PRUNE_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "features_version": self.features.features_version,
            "features": self.features.as_dict(),
            "effect_scope": (
                "production_default_recommendation_demotion;"
                "exploration_space_remains_open"
            ),
            "hits": [
                {
                    "rule_id": h.rule_id,
                    "reason": h.reason,
                    "effect": h.effect,
                    "demoted_template_ids": list(h.demoted_template_ids),
                    "excluded_template_ids": list(h.excluded_template_ids),
                    "demotion_penalty": h.demotion_penalty,
                }
                for h in self.hits
            ],
            "admitted_template_ids": [
                str(c.get("template_id") or (c.get("topology_json") or {}).get("template_id"))
                for c in self.admitted
            ],
            "demoted_template_ids": [
                str(c.get("template_id") or (c.get("topology_json") or {}).get("template_id"))
                for c in self.demoted
            ],
            "excluded_template_ids": [
                str(c.get("template_id") or (c.get("topology_json") or {}).get("template_id"))
                for c in self.excluded
            ],
            "demotion_penalties": dict(self.demotion_penalties),
            "production_default_recommended_template_ids": list(
                self.production_default_recommended_template_ids
            ),
        }


def _template_id(candidate: Mapping[str, Any]) -> str:
    topo = candidate.get("topology_json") or candidate
    return str(
        candidate.get("template_id")
        or candidate.get("name")
        or (topo.get("template_id") if isinstance(topo, Mapping) else None)
        or ""
    )


def _strategy(candidate: Mapping[str, Any]) -> str:
    topo = candidate.get("topology_json") or candidate
    if isinstance(topo, Mapping):
        return str(topo.get("strategy") or candidate.get("strategy") or "")
    return str(candidate.get("strategy") or "")


def _is_multi_agent(candidate: Mapping[str, Any]) -> bool:
    topo = candidate.get("topology_json") or candidate
    roles = []
    if isinstance(topo, Mapping):
        roles = list(topo.get("roles") or [])
    strategy = _strategy(candidate)
    if strategy == "SINGLE_AGENT":
        return False
    return len(roles) > 1 or strategy == "FIXED_TEMPLATE"


def _needs_independent_verification(candidate: Mapping[str, Any]) -> bool:
    topo = candidate.get("topology_json") or candidate
    if not isinstance(topo, Mapping):
        return False
    for role in topo.get("roles") or []:
        if role.get("independent_reviewer") or role.get("role") in {"qa", "reviewer"}:
            return True
    return "producer_reviewer_separation" in set(topo.get("invariants") or [])


def _annotate_demotion(
    candidate: dict[str, Any],
    *,
    rule_id: str,
    reason: str,
    penalty: float,
) -> dict[str, Any]:
    annotated = dict(candidate)
    prior = list(annotated.get("production_default_demotions") or [])
    prior.append(
        {
            "rule_id": rule_id,
            "reason": reason,
            "demotion_penalty": penalty,
        }
    )
    annotated["production_default_demotions"] = prior
    annotated["production_default_demotion_penalty"] = round(
        float(annotated.get("production_default_demotion_penalty") or 0.0) + penalty,
        4,
    )
    # Soft production-default flag only; sandbox / OrganizationExperiment may still run.
    annotated["not_recommended_for_production_default"] = True
    return annotated


def prune_organization_space(
    candidates: Sequence[Mapping[str, Any]],
    features: TaskFeatures,
) -> OrganizationSpacePruneResult:
    """Admit all exploration candidates; demote multi-agent for production default.

    Single-agent templates remain the production-default bias champion when present.
    Multi-agent topologies stay in ``admitted`` for sandbox / OrganizationExperiment.
    """
    admitted: list[dict[str, Any]] = []
    demoted: list[dict[str, Any]] = []
    hits: list[PruneHit] = []
    demotion_penalties: dict[str, float] = {}
    production_default_ids: list[str] = []

    for raw in candidates:
        candidate = dict(raw)
        tid = _template_id(candidate)
        multi = _is_multi_agent(candidate)

        if not multi:
            admitted.append(candidate)
            if tid:
                production_default_ids.append(tid)
            continue

        demotion: tuple[str, str, float] | None = None

        # Rule 1: high single-agent baseline → demote (do not ban) multi-agent default.
        if features.single_agent_baseline_success_rate > SINGLE_AGENT_BASELINE_THRESHOLD:
            demotion = (
                "R1_HIGH_SINGLE_AGENT_BASELINE",
                (
                    f"single_agent_baseline_success_rate="
                    f"{features.single_agent_baseline_success_rate} > "
                    f"{SINGLE_AGENT_BASELINE_THRESHOLD}; "
                    "production-default recommendation demoted, exploration still open"
                ),
                DEMOTION_PENALTY_R1_HIGH_BASELINE,
            )
        # Rule 2: strong sequential dependency → prefer single-agent default.
        elif features.sequential_dependency_score >= 0.7:
            demotion = (
                "R2_STRONG_SEQUENTIAL",
                (
                    f"sequential_dependency_score="
                    f"{features.sequential_dependency_score} >= 0.7; "
                    "production-default recommendation demoted, exploration still open"
                ),
                DEMOTION_PENALTY_R2_STRONG_SEQUENTIAL,
            )
        # Rule 3: verification/decomposability priors bias topology choice, not admission.
        elif features.independent_verification_required and features.decomposability_score >= 0.5:
            if _needs_independent_verification(candidate):
                if features.estimated_parallelism_ceiling <= 0.0:
                    demotion = (
                        "R3_NO_PARALLEL_CEILING",
                        (
                            "estimated_parallelism_ceiling <= 0; "
                            "production-default recommendation demoted, "
                            "exploration still open"
                        ),
                        DEMOTION_PENALTY_R3,
                    )
            else:
                demotion = (
                    "R3_MISSING_INDEPENDENT_VERIFIER",
                    (
                        "verification required but template lacks independent reviewer; "
                        "production-default recommendation demoted, exploration still open"
                    ),
                    DEMOTION_PENALTY_R3,
                )
        else:
            # Soft bias toward single-agent production default when priors do not
            # specially justify multi-agent — still admit for sandbox exploration.
            demotion = (
                "R0_DEFAULT_SINGLE_AGENT",
                (
                    "multi-agent not preferred by frozen production-default priors; "
                    "demoted for recommendation only, exploration still open"
                ),
                DEMOTION_PENALTY_R0_DEFAULT,
            )

        if demotion is not None:
            rule_id, reason, penalty = demotion
            candidate = _annotate_demotion(
                candidate, rule_id=rule_id, reason=reason, penalty=penalty
            )
            demoted.append(candidate)
            demotion_penalties[tid] = float(
                candidate.get("production_default_demotion_penalty") or penalty
            )
            hits.append(
                PruneHit(
                    rule_id=rule_id,
                    reason=reason,
                    effect="demote",
                    demoted_template_ids=[tid],
                    excluded_template_ids=[],
                    demotion_penalty=penalty,
                )
            )
        admitted.append(candidate)

    if not production_default_ids and admitted:
        hits.append(
            PruneHit(
                rule_id="R_NO_SAFE_SINGLE_AGENT_FALLBACK",
                reason=(
                    "no single-agent champion among candidates; "
                    "exploration admits multi-agent, but production-default "
                    "recommendation lacks a safe single-agent bias target"
                ),
                effect="warn",
                demoted_template_ids=[],
                excluded_template_ids=[],
                demotion_penalty=0.0,
            )
        )

    return OrganizationSpacePruneResult(
        admitted=admitted,
        excluded=[],
        demoted=demoted,
        hits=hits,
        features=features,
        demotion_penalties=demotion_penalties,
        production_default_recommended_template_ids=production_default_ids,
    )
