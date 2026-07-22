from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    reason: str


class GoalEligibilityService:
    def evaluate(
        self, metadata: dict[str, Any], constraints: dict[str, Any]
    ) -> EligibilityDecision:
        if metadata.get("goal_type") != "product_creation":
            return EligibilityDecision(False, "goal_type must be product_creation")
        if constraints.get("autonomous_product_creation") is False:
            return EligibilityDecision(False, "product creation disabled by root constraint")
        return EligibilityDecision(True, "eligible")
