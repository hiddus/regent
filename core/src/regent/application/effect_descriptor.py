"""Multi-dimensional classification for real-world effects."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class EffectRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_SCORE = {
    EffectRisk.LOW: 0,
    EffectRisk.MEDIUM: 1,
    EffectRisk.HIGH: 2,
    EffectRisk.CRITICAL: 3,
}


@dataclass(frozen=True, slots=True)
class EffectDescriptor:
    """Describe impact without reducing it to reversible/irreversible."""

    data_sensitivity: EffectRisk = EffectRisk.LOW
    external_visibility: EffectRisk = EffectRisk.LOW
    financial_impact: EffectRisk = EffectRisk.LOW
    affected_people: int = 0
    blast_radius: EffectRisk = EffectRisk.LOW
    compensatable: bool = True
    writes_shared_state: bool = False
    contacts_people: bool = False
    network_egress: bool = False
    legal_or_safety_decision: bool = False
    cumulative_count: int = 1
    purpose: str = ""

    def risk_tier(self) -> EffectRisk:
        score = max(
            _SCORE[self.data_sensitivity],
            _SCORE[self.external_visibility],
            _SCORE[self.financial_impact],
            _SCORE[self.blast_radius],
        )
        if self.legal_or_safety_decision:
            score = max(score, _SCORE[EffectRisk.CRITICAL])
        if not self.compensatable:
            score = max(score, _SCORE[EffectRisk.HIGH])
        if self.affected_people > 100 or self.cumulative_count > 100:
            score = max(score, _SCORE[EffectRisk.HIGH])
        elif self.affected_people > 0 or self.cumulative_count > 10:
            score = max(score, _SCORE[EffectRisk.MEDIUM])
        if self.contacts_people or self.writes_shared_state or self.network_egress:
            score = max(score, _SCORE[EffectRisk.MEDIUM])
        return next(risk for risk, value in _SCORE.items() if value == score)

    def requires_permit(self) -> bool:
        return self.risk_tier() is not EffectRisk.LOW

    def policy_resource(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_tier"] = self.risk_tier().value
        payload["effect_model"] = "effect-descriptor/v1"
        return payload
