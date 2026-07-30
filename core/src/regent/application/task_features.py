"""Organization routing TaskFeatures and candidate pruning (PRD §10.1 / Spec §18.1).

Rules only prune the candidate space; they never replace the P2-4 statistical Gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

TASK_FEATURES_VERSION = "task-features/v1"
PRUNE_POLICY_VERSION = "org-space-prune/v1"
SINGLE_AGENT_BASELINE_THRESHOLD = 0.45


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
    excluded_template_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OrganizationSpacePruneResult:
    admitted: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    hits: list[PruneHit]
    features: TaskFeatures
    policy_version: str = PRUNE_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "features_version": self.features.features_version,
            "features": self.features.as_dict(),
            "hits": [
                {
                    "rule_id": h.rule_id,
                    "reason": h.reason,
                    "excluded_template_ids": list(h.excluded_template_ids),
                }
                for h in self.hits
            ],
            "admitted_template_ids": [
                str(c.get("template_id") or (c.get("topology_json") or {}).get("template_id"))
                for c in self.admitted
            ],
            "excluded_template_ids": [
                str(c.get("template_id") or (c.get("topology_json") or {}).get("template_id"))
                for c in self.excluded
            ],
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


def prune_organization_space(
    candidates: Sequence[Mapping[str, Any]],
    features: TaskFeatures,
) -> OrganizationSpacePruneResult:
    """Apply frozen prune rules; keep single-agent champion always admitted."""
    admitted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    hits: list[PruneHit] = []

    for raw in candidates:
        candidate = dict(raw)
        tid = _template_id(candidate)
        multi = _is_multi_agent(candidate)

        # Always keep single-agent templates.
        if not multi:
            admitted.append(candidate)
            continue

        # Rule 1: high single-agent baseline → do not default-recommend multi-agent.
        if features.single_agent_baseline_success_rate > SINGLE_AGENT_BASELINE_THRESHOLD:
            excluded.append(candidate)
            hits.append(
                PruneHit(
                    rule_id="R1_HIGH_SINGLE_AGENT_BASELINE",
                    reason=(
                        f"single_agent_baseline_success_rate="
                        f"{features.single_agent_baseline_success_rate} > "
                        f"{SINGLE_AGENT_BASELINE_THRESHOLD}"
                    ),
                    excluded_template_ids=[tid],
                )
            )
            continue

        # Rule 2: strong sequential dependency → keep single agent.
        if features.sequential_dependency_score >= 0.7:
            excluded.append(candidate)
            hits.append(
                PruneHit(
                    rule_id="R2_STRONG_SEQUENTIAL",
                    reason=(
                        f"sequential_dependency_score="
                        f"{features.sequential_dependency_score} >= 0.7"
                    ),
                    excluded_template_ids=[tid],
                )
            )
            continue

        # Rule 3: only when decomposable AND independent verification required,
        # admit centralized orchestration + independent verification templates.
        if features.independent_verification_required and features.decomposability_score >= 0.5:
            if _needs_independent_verification(candidate):
                if features.estimated_parallelism_ceiling <= 0.0:
                    excluded.append(candidate)
                    hits.append(
                        PruneHit(
                            rule_id="R3_NO_PARALLEL_CEILING",
                            reason="estimated_parallelism_ceiling <= 0",
                            excluded_template_ids=[tid],
                        )
                    )
                    continue
                admitted.append(candidate)
                continue
            excluded.append(candidate)
            hits.append(
                PruneHit(
                    rule_id="R3_MISSING_INDEPENDENT_VERIFIER",
                    reason="verification required but template lacks independent reviewer",
                    excluded_template_ids=[tid],
                )
            )
            continue

        # Otherwise multi-agent is not admitted by prior rules.
        excluded.append(candidate)
        hits.append(
            PruneHit(
                rule_id="R0_DEFAULT_SINGLE_AGENT",
                reason="multi-agent not justified by frozen prior rules",
                excluded_template_ids=[tid],
            )
        )

    # Safety: if everything multi was pruned and no single-agent left, keep first.
    if not admitted and candidates:
        admitted = [dict(candidates[0])]
        hits.append(
            PruneHit(
                rule_id="R_SAFE_FALLBACK",
                reason="no admitted candidates; fallback to first",
                excluded_template_ids=[],
            )
        )

    return OrganizationSpacePruneResult(
        admitted=admitted,
        excluded=excluded,
        hits=hits,
        features=features,
    )
