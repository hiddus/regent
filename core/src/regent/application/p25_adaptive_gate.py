"""P2-5 adaptive organization conditional activation gate (MA-6).

Production *rollout* of adaptive free-form topology remains ROLLOUT_NOT_ALLOWED
until a P2-4 DecisionRecord proves positive net benefit. That gate constrains
production-default / real-world permission expansion only — it does not block
sandbox OrganizationSpace exploration or OrganizationExperiment candidates
(PRD §10.1 / REGENT-DEFINITION-3.0 ATTRIBUTE_2/7). This module only provides
gate hooks and skeleton proposal enrichment — it never activates production
rollout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from regent.application.task_features import TaskFeatures, prune_organization_space

P25_GATE_VERSION = "p25-adaptive-gate/v1"
ROLLOUT_NOT_ALLOWED = "ROLLOUT_NOT_ALLOWED"
GO_DECISIONS = frozenset(
    {
        "GO_ADAPTIVE_ORG",
        "PROMOTE_ADAPTIVE_ORGANIZATION",
        "POSITIVE_NET_ENABLE_P25",
    }
)


@dataclass(frozen=True, slots=True)
class AdaptiveGateResult:
    allowed: bool
    status: str
    reason: str
    gate_version: str = P25_GATE_VERSION
    decision_record_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "reason": self.reason,
            "gate_version": self.gate_version,
            "decision_record_ref": self.decision_record_ref,
        }


def evaluate_adaptive_rollout_gate(
    decision_record: Mapping[str, Any] | None,
) -> AdaptiveGateResult:
    """Return whether P2-5 adaptive topology may be developed/enabled.

    Without an explicit GO DecisionRecord, always deny.
    """
    if not decision_record:
        return AdaptiveGateResult(
            allowed=False,
            status=ROLLOUT_NOT_ALLOWED,
            reason="missing_p24_decision_record",
        )
    decision = str(
        decision_record.get("decision")
        or decision_record.get("verdict")
        or ""
    ).upper()
    adaptive_status = str(
        decision_record.get("org_adaptive_status") or ROLLOUT_NOT_ALLOWED
    )
    if adaptive_status == ROLLOUT_NOT_ALLOWED and decision not in GO_DECISIONS:
        return AdaptiveGateResult(
            allowed=False,
            status=ROLLOUT_NOT_ALLOWED,
            reason=f"decision={decision or 'unset'}; adaptive still gated",
            decision_record_ref=str(decision_record.get("content_hash") or "") or None,
        )
    if decision in GO_DECISIONS and adaptive_status != ROLLOUT_NOT_ALLOWED:
        return AdaptiveGateResult(
            allowed=True,
            status="GATE_PASSED",
            reason="p24_positive_net_decision_record",
            decision_record_ref=str(decision_record.get("content_hash") or "") or None,
        )
    return AdaptiveGateResult(
        allowed=False,
        status=ROLLOUT_NOT_ALLOWED,
        reason="gate_requires_explicit_go_and_non_blocked_adaptive_status",
        decision_record_ref=str(decision_record.get("content_hash") or "") or None,
    )


def enrich_adaptive_proposal_skeleton(
    proposal: Mapping[str, Any],
    *,
    features: TaskFeatures | None = None,
    candidates: list[dict[str, Any]] | None = None,
    decision_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach prune/gate metadata to an adaptive proposal without activating it."""
    gate = evaluate_adaptive_rollout_gate(decision_record)
    enriched = dict(proposal)
    enriched["rollout_gate"] = gate.status
    enriched["p25_gate"] = gate.as_dict()
    enriched["gate_version"] = P25_GATE_VERSION
    if features is not None and candidates is not None:
        prune = prune_organization_space(candidates, features)
        enriched["task_features"] = features.as_dict()
        enriched["organization_space_prune"] = prune.as_dict()
    # Hard enforce: never flip allowed activation in this skeleton.
    enriched["activation_allowed"] = False
    if not gate.allowed:
        enriched["activation_allowed"] = False
        enriched["rollout_gate"] = ROLLOUT_NOT_ALLOWED
    return enriched
