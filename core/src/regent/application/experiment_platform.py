"""P3-B: Champion/Challenger experiment platform.

Supports:
- Experiment creation with traffic allocation
- Statistical significance computation
- Blind evaluation integration
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Configuration for a Champion/Challenger experiment."""

    experiment_id: uuid.UUID
    name: str
    champion_variant: str
    challenger_variant: str
    traffic_split: float = 0.5  # fraction going to challenger
    min_samples: int = 30
    confidence_level: float = 0.95
    metrics: list[str] = field(default_factory=lambda: ["pass_rate", "latency_p50"])


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """Result of an experiment evaluation."""

    experiment_id: uuid.UUID
    champion_score: float
    challenger_score: float
    p_value: float
    significant: bool
    recommendation: str  # "KEEP_CHAMPION" | "PROMOTE_CHALLENGER" | "INCONCLUSIVE"
    samples_champion: int
    samples_challenger: int
    confidence_level: float


class ExperimentPlatform:
    """Champion/Challenger experiment platform for A/B testing variants."""

    def __init__(self) -> None:
        self._experiments: dict[uuid.UUID, dict[str, Any]] = {}
        self._results: dict[uuid.UUID, list[dict[str, Any]]] = {}

    def create_experiment(
        self,
        *,
        name: str,
        champion_variant: str,
        challenger_variant: str,
        traffic_split: float = 0.5,
        min_samples: int = 30,
        confidence_level: float = 0.95,
    ) -> ExperimentConfig:
        """Create a new Champion/Challenger experiment."""
        config = ExperimentConfig(
            experiment_id=uuid.uuid4(),
            name=name,
            champion_variant=champion_variant,
            challenger_variant=challenger_variant,
            traffic_split=traffic_split,
            min_samples=min_samples,
            confidence_level=confidence_level,
        )
        self._experiments[config.experiment_id] = {
            "config": config,
            "champion_results": [],
            "challenger_results": [],
        }
        return config

    def record_result(
        self,
        experiment_id: uuid.UUID,
        *,
        variant: str,
        metric: str,
        value: float,
    ) -> None:
        """Record a result for a variant in an experiment."""
        if experiment_id not in self._experiments:
            raise ValueError(f"experiment {experiment_id} not found")
        entry = {"variant": variant, "metric": metric, "value": value}
        exp = self._experiments[experiment_id]
        if variant == exp["config"].champion_variant:
            exp["champion_results"].append(entry)
        elif variant == exp["config"].challenger_variant:
            exp["challenger_results"].append(entry)

    def analyze(
        self,
        experiment_id: uuid.UUID,
        *,
        metric: str = "pass_rate",
    ) -> ExperimentResult:
        """Analyze experiment results for statistical significance.

        Uses a two-proportion z-test for pass_rate metric.
        """
        if experiment_id not in self._experiments:
            raise ValueError(f"experiment {experiment_id} not found")

        exp = self._experiments[experiment_id]
        config = exp["config"]

        champion_vals = [
            r["value"] for r in exp["champion_results"] if r["metric"] == metric
        ]
        challenger_vals = [
            r["value"] for r in exp["challenger_results"] if r["metric"] == metric
        ]

        n_c = len(champion_vals)
        n_t = len(challenger_vals)

        if n_c < config.min_samples or n_t < config.min_samples:
            return ExperimentResult(
                experiment_id=experiment_id,
                champion_score=sum(champion_vals) / max(n_c, 1),
                challenger_score=sum(challenger_vals) / max(n_t, 1),
                p_value=1.0,
                significant=False,
                recommendation="INCONCLUSIVE",
                samples_champion=n_c,
                samples_challenger=n_t,
                confidence_level=config.confidence_level,
            )

        p_c = sum(champion_vals) / n_c
        p_t = sum(challenger_vals) / n_t
        p_pool = (sum(champion_vals) + sum(challenger_vals)) / (n_c + n_t)

        # Two-proportion z-test
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_c + 1 / n_t)) if p_pool > 0 else 1.0
        z = (p_t - p_c) / se if se > 0 else 0.0

        # Approximate p-value from z-score (two-tailed)
        p_value = 2 * (1 - _normal_cdf(abs(z)))

        alpha = 1 - config.confidence_level
        significant = p_value < alpha

        if significant and p_t > p_c:
            recommendation = "PROMOTE_CHALLENGER"
        elif significant and p_t <= p_c:
            recommendation = "KEEP_CHAMPION"
        else:
            recommendation = "INCONCLUSIVE"

        return ExperimentResult(
            experiment_id=experiment_id,
            champion_score=round(p_c, 4),
            challenger_score=round(p_t, 4),
            p_value=round(p_value, 4),
            significant=significant,
            recommendation=recommendation,
            samples_champion=n_c,
            samples_challenger=n_t,
            confidence_level=config.confidence_level,
        )

    def allocate_traffic(
        self,
        experiment_id: uuid.UUID,
        *,
        request_id: str,
    ) -> str:
        """Allocate a request to champion or challenger based on traffic split."""
        if experiment_id not in self._experiments:
            raise ValueError(f"experiment {experiment_id} not found")
        config = self._experiments[experiment_id]["config"]
        # Deterministic allocation based on request_id hash
        import hashlib
        h = int(hashlib.md5(request_id.encode()).hexdigest()[:8], 16)
        ratio = (h % 10000) / 10000.0
        if ratio < config.traffic_split:
            return config.challenger_variant
        return config.champion_variant


def _normal_cdf(x: float) -> float:
    """Approximate the standard normal CDF using error function approximation."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
