"""MAST failure_code namespace for multi-agent structural failures (Spec §18.4).

Classifier attaches trajectory refs + confidence. Low confidence keeps the
original failure_code and does not overwrite factual errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

MAST_NAMESPACE = "MAST_"
MAST_CLASSIFIER_VERSION = "mast-classifier-v1"
DEFAULT_CONFIDENCE_THRESHOLD = 0.70

MAST_CODES: tuple[str, ...] = (
    "MAST_STEP_REPETITION",
    "MAST_PREMATURE_TERMINATION",
    "MAST_ROLE_BOUNDARY_VIOLATION",
    "MAST_REASONING_ACTION_MISMATCH",
    "MAST_CLARIFICATION_NOT_REQUESTED",
    "MAST_IGNORED_PEER_OUTPUT",
    "MAST_IMPLICIT_DECISION_CONFLICT",
    "MAST_VERIFICATION_MISSING",
    "MAST_VERIFIER_FAILURE",
)

_MAST_SET = frozenset(MAST_CODES)


@dataclass(frozen=True, slots=True)
class MastClassification:
    mast_code: str | None
    confidence: float
    trajectory_refs: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    retained_original_code: str | None = None
    low_confidence: bool = False
    classifier_version: str = MAST_CLASSIFIER_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "mast_code": self.mast_code,
            "confidence": self.confidence,
            "trajectory_refs": list(self.trajectory_refs),
            "evidence": dict(self.evidence),
            "retained_original_code": self.retained_original_code,
            "low_confidence": self.low_confidence,
            "classifier_version": self.classifier_version,
            "effective_failure_code": self.effective_failure_code(),
        }

    def effective_failure_code(self) -> str | None:
        if self.low_confidence or self.mast_code is None:
            return self.retained_original_code
        return self.mast_code


def is_mast_code(code: str | None) -> bool:
    return bool(code) and code in _MAST_SET


def classify_mast_failure(
    *,
    signals: Mapping[str, Any],
    original_failure_code: str | None = None,
    trajectory_refs: Sequence[str] | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> MastClassification:
    """Map structural collaboration signals to a stable MAST_ code.

    Expected signal keys (booleans / ints):
    - repeated_step_count, premature_stop, role_boundary_violation,
      reasoning_action_mismatch, uncertainty_high, clarification_requested,
      peer_output_ignored, implicit_decision_conflict, verification_present,
      verifier_failed
    """
    refs = list(trajectory_refs or signals.get("trajectory_refs") or [])
    candidates: list[tuple[str, float, dict[str, Any]]] = []

    repeated = int(signals.get("repeated_step_count") or 0)
    if repeated >= 2:
        candidates.append(
            (
                "MAST_STEP_REPETITION",
                min(1.0, 0.55 + 0.15 * repeated),
                {"repeated_step_count": repeated},
            )
        )

    if signals.get("premature_stop"):
        candidates.append(
            (
                "MAST_PREMATURE_TERMINATION",
                0.85 if signals.get("open_todos") else 0.65,
                {"premature_stop": True, "open_todos": signals.get("open_todos")},
            )
        )

    if signals.get("role_boundary_violation"):
        candidates.append(
            (
                "MAST_ROLE_BOUNDARY_VIOLATION",
                0.9,
                {"role_boundary_violation": True, "role": signals.get("role")},
            )
        )

    if signals.get("reasoning_action_mismatch"):
        candidates.append(
            (
                "MAST_REASONING_ACTION_MISMATCH",
                0.8,
                {"reasoning_action_mismatch": True},
            )
        )

    if signals.get("uncertainty_high") and not signals.get("clarification_requested"):
        candidates.append(
            (
                "MAST_CLARIFICATION_NOT_REQUESTED",
                0.82,
                {
                    "uncertainty_high": True,
                    "clarification_requested": False,
                },
            )
        )

    if signals.get("peer_output_ignored"):
        candidates.append(
            (
                "MAST_IGNORED_PEER_OUTPUT",
                0.88,
                {"peer_output_ignored": True, "peer_ref": signals.get("peer_ref")},
            )
        )

    if signals.get("implicit_decision_conflict"):
        candidates.append(
            (
                "MAST_IMPLICIT_DECISION_CONFLICT",
                0.84,
                {"implicit_decision_conflict": True},
            )
        )

    if signals.get("verification_required") and not signals.get("verification_present"):
        candidates.append(
            (
                "MAST_VERIFICATION_MISSING",
                0.9,
                {"verification_required": True, "verification_present": False},
            )
        )

    if signals.get("verifier_failed"):
        candidates.append(
            (
                "MAST_VERIFIER_FAILURE",
                0.92,
                {"verifier_failed": True},
            )
        )

    if not candidates:
        return MastClassification(
            mast_code=None,
            confidence=0.0,
            trajectory_refs=refs,
            evidence={"signals": dict(signals)},
            retained_original_code=original_failure_code,
            low_confidence=True,
        )

    candidates.sort(key=lambda c: (-c[1], c[0]))
    code, confidence, evidence = candidates[0]
    low = confidence < confidence_threshold
    return MastClassification(
        mast_code=None if low else code,
        confidence=round(confidence, 4),
        trajectory_refs=refs,
        evidence={**evidence, "runner_up": [c[0] for c in candidates[1:3]]},
        retained_original_code=original_failure_code,
        low_confidence=low,
    )
