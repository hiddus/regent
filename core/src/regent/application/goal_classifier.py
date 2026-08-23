"""Goal classifier — analyze goal characteristics to select execution mode.

Instead of forcing all goals through the same waterfall pipeline
(Discovery → Requirement → Capability → Generation → Build → Preview),
this module examines the goal's features and recommends the most appropriate
organization mode.

Design principles:
- Rule-based first; LLM-assisted only when rules are inconclusive.
- Classification is a recommendation, not a mandate — the orchestrator
  may override based on runtime conditions.
- Results are stored in ``goal.metadata_json["goal_profile"]`` for
  downstream components (orchestrator, monitor, repair loop).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Goal profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GoalProfile:
    """Multi-dimensional characterization of a goal."""

    scale: str = "MEDIUM"               # SMALL / MEDIUM / LARGE
    domain: str = "other"               # static-web / interactive-app / api-service / data-pipeline / other
    complexity: str = "MEDIUM"          # LOW / MEDIUM / HIGH
    iteration_need: str = "LIGHT"       # NONE / LIGHT / HEAVY
    monitoring_need: str = "BASIC"      # NONE / BASIC / CONTINUOUS
    confidence: float = 0.7             # 0.0–1.0; how confident the classifier is
    signals: list[str] = field(default_factory=list)  # evidence that led to this profile

    def as_dict(self) -> dict[str, Any]:
        return {
            "scale": self.scale,
            "domain": self.domain,
            "complexity": self.complexity,
            "iteration_need": self.iteration_need,
            "monitoring_need": self.monitoring_need,
            "confidence": self.confidence,
            "signals": list(self.signals),
        }


# ---------------------------------------------------------------------------
# Domain detection patterns
# ---------------------------------------------------------------------------

_STATIC_WEB_HINTS = re.compile(
    r"(?:静态|单页|landing\s+page|index\.html|styles\.css|纯前端|html.*css| brochure|"
    r"展示页|宣传页|个人主页|简历|portfolio|static\s+site)",
    re.I,
)
_INTERACTIVE_APP_HINTS = re.compile(
    r"(?:交互|小镇|游戏|dashboard|应用|app|动态|模拟|simulator|虚拟|"
    r"对话|角色|人物|chat|实时|real.?time|town|village|world)",
    re.I,
)
_API_SERVICE_HINTS = re.compile(
    r"(?:api|后端|服务|微服务|microservice|endpoint|rest|graphql|"
    r"数据库|database|crud|认证|auth|鉴权)",
    re.I,
)
_DATA_PIPELINE_HINTS = re.compile(
    r"(?:数据|etl|pipeline|批处理|batch|清洗|transform|"
    r"分析|analytics|报表|report|聚合|aggregate)",
    re.I,
)

# Complexity signals
_HIGH_COMPLEXITY_HINTS = re.compile(
    r"(?:多模块|multi.?module|微服务|microservice|分布式|distributed|"
    r"高并发|high.?concurrency|安全|security|支付|payment|"
    r"复杂|complex|企业级|enterprise)",
    re.I,
)
_LOW_COMPLEXITY_HINTS = re.compile(
    r"(?:简单|simple|快速|quick|小|small|基础|basic|"
    r"demo|原型|prototype|示例|example)",
    re.I,
)

# Iteration need signals
_HEAVY_ITERATION_HINTS = re.compile(
    r"(?:迭代|iterative|持续|continuous|不断|evolv|改进|improve|"
    r"优化|optimize|监控|monitor|反馈|feedback|自适应|adaptive)",
    re.I,
)

# Monitoring need signals
_CONTINUOUS_MONITOR_HINTS = re.compile(
    r"(?:实时监控|real.?time\s+monitor|持续观察|continuously|"
    r"自动检测|auto.?detect|异常|anomaly|告警|alert|"
    r"自治|self.?heal|自愈|自动修复|auto.?repair)",
    re.I,
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class GoalClassifier:
    """Classify a goal into a multi-dimensional profile.

    Uses lightweight text analysis on the goal input, spec, and metadata.
    No LLM calls — this runs synchronously during goal setup.
    """

    def classify(
        self,
        goal_input: str = "",
        *,
        spec_constraints: dict[str, Any] | None = None,
        spec_success_criteria: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        goal_scale: str | None = None,
    ) -> GoalProfile:
        """Analyze goal characteristics and return a profile."""
        meta = metadata or {}
        signals: list[str] = []

        # --- Scale ---
        scale = self._detect_scale(goal_input, meta, goal_scale)
        if scale:
            signals.append(f"scale={scale}")

        # --- Domain ---
        domain, domain_conf = self._detect_domain(goal_input, meta)
        signals.append(f"domain={domain}({domain_conf:.1f})")

        # --- Complexity ---
        complexity, cx_conf = self._detect_complexity(goal_input, meta)
        signals.append(f"complexity={complexity}({cx_conf:.1f})")

        # --- Iteration need ---
        iteration = self._detect_iteration_need(goal_input, meta)
        signals.append(f"iteration={iteration}")

        # --- Monitoring need ---
        monitoring = self._detect_monitoring_need(goal_input, meta)
        signals.append(f"monitoring={monitoring}")

        # --- Overall confidence ---
        confidence = min(1.0, (domain_conf + cx_conf) / 2)
        if goal_input and len(goal_input.strip()) > 20:
            confidence = min(1.0, confidence + 0.1)

        return GoalProfile(
            scale=scale,
            domain=domain,
            complexity=complexity,
            iteration_need=iteration,
            monitoring_need=monitoring,
            confidence=round(confidence, 2),
            signals=signals,
        )

    # --- Private helpers ---

    def _detect_scale(
        self,
        goal_input: str,
        metadata: dict[str, Any],
        goal_scale: str | None = None,
    ) -> str:
        # Explicit metadata wins.
        explicit = str(metadata.get("goal_scale") or goal_scale or "").upper()
        if explicit in {"SMALL", "MEDIUM", "LARGE"}:
            return explicit
        # Heuristic: short input + few criteria → SMALL.
        text_len = len(goal_input.strip())
        if text_len <= 100:
            return "SMALL"
        if text_len >= 800:
            return "LARGE"
        return "MEDIUM"

    def _detect_domain(
        self,
        goal_input: str,
        metadata: dict[str, Any],
    ) -> tuple[str, float]:
        text = f"{goal_input} {metadata.get('title', '')} {metadata.get('project_kind', '')}"
        scores = {
            "static-web": len(_STATIC_WEB_HINTS.findall(text)),
            "interactive-app": len(_INTERACTIVE_APP_HINTS.findall(text)),
            "api-service": len(_API_SERVICE_HINTS.findall(text)),
            "data-pipeline": len(_DATA_PIPELINE_HINTS.findall(text)),
        }
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        if scores[best] == 0:
            return "other", 0.4
        total = sum(scores.values()) or 1
        return best, scores[best] / total

    def _detect_complexity(
        self,
        goal_input: str,
        metadata: dict[str, Any],
    ) -> tuple[str, float]:
        text = f"{goal_input} {metadata.get('title', '')}"
        high = len(_HIGH_COMPLEXITY_HINTS.findall(text))
        low = len(_LOW_COMPLEXITY_HINTS.findall(text))
        if high > low and high >= 2:
            return "HIGH", min(1.0, high / 4)
        if low > high and low >= 1:
            return "LOW", min(1.0, low / 3)
        return "MEDIUM", 0.5

    def _detect_iteration_need(
        self,
        goal_input: str,
        metadata: dict[str, Any],
    ) -> str:
        text = f"{goal_input} {metadata.get('title', '')}"
        hits = len(_HEAVY_ITERATION_HINTS.findall(text))
        if hits >= 3:
            return "HEAVY"
        if hits >= 1:
            return "LIGHT"
        return "NONE"

    def _detect_monitoring_need(
        self,
        goal_input: str,
        metadata: dict[str, Any],
    ) -> str:
        text = f"{goal_input} {metadata.get('title', '')}"
        if _CONTINUOUS_MONITOR_HINTS.search(text):
            return "CONTINUOUS"
        # Interactive apps and complex domains benefit from basic monitoring.
        domain, _ = self._detect_domain(goal_input, metadata)
        if domain == "interactive-app":
            return "BASIC"
        return "NONE"
