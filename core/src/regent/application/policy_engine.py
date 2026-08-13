"""AAR-1 deterministic Policy Engine (DSL v1).

Fail-closed: missing version, missing input, or evaluation exception → DENY.
Priority: SYSTEM → ORG → PROJECT → GOAL intersection; any DENY wins.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import PolicyEvaluationModel

EVALUATOR_VERSION = "policy-engine/v1"

DECISION_POINTS = frozenset(
    {
        "GOAL_CONFIRM",
        "ORG_CANDIDATE_ADMISSION",
        "ORG_ACTIVATION",
        "AGENT_CERTIFICATION",
        "AGENT_DEPLOYMENT",
        "A2A_DELEGATION",
        "MCP_TOOL_DISCOVERY",
        "MCP_TOOL_INVOKE",
        "EXTERNAL_EFFECT_PREPARE",
        "RELEASE",
        "MEMORY_PROMOTION",
    }
)

SCOPE_ORDER = ("SYSTEM", "ORG", "PROJECT", "GOAL")


class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_PERMIT = "REQUIRE_PERMIT"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    id: str
    decision_point: str
    effect: str
    subject: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    resource: dict[str, Any] = field(default_factory=dict)
    obligations: dict[str, Any] = field(default_factory=dict)
    scope_type: str = "SYSTEM"


@dataclass(frozen=True, slots=True)
class PolicyEvaluationRequest:
    decision_point: str
    subject_type: str
    subject_id: str
    action: str
    resource: dict[str, Any]
    input_snapshot: dict[str, Any]
    rules: list[PolicyRule]
    constitution_version_id: uuid.UUID | None = None
    correlation_id: str = ""
    causation_id: str | None = None
    role: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyEvaluationResult:
    id: uuid.UUID
    outcome: PolicyOutcome
    matched_rule_ids: list[str]
    obligations: dict[str, Any]
    reason_codes: list[str]
    input_hash: str
    evaluator_version: str = EVALUATOR_VERSION


def canonical_hash(value: dict[str, Any] | list[Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _match_equals(spec: dict[str, Any], key: str, actual: Any) -> bool:
    if key not in spec:
        return True
    return actual == spec[key]


def _match_in(spec: dict[str, Any], key: str, actual: Any) -> bool:
    if key not in spec:
        return True
    allowed = spec[key]
    if not isinstance(allowed, list):
        return False
    return actual in allowed


def _match_risk_tier_gte(spec: dict[str, Any], resource: dict[str, Any]) -> bool:
    if "risk_tier_gte" not in spec:
        return True
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    required = order.get(str(spec["risk_tier_gte"]).upper(), 99)
    actual = order.get(str(resource.get("risk_tier", "LOW")).upper(), 0)
    return actual >= required


def rule_matches(rule: PolicyRule, request: PolicyEvaluationRequest) -> bool:
    if rule.decision_point != request.decision_point:
        return False
    if not _match_in(rule.subject, "role_in", request.role):
        return False
    if not _match_equals(rule.action, "equals", request.action):
        return False
    if not _match_risk_tier_gte(rule.resource, request.resource):
        return False
    for key, expected in rule.resource.items():
        if key == "risk_tier_gte":
            continue
        if request.resource.get(key) != expected:
            return False
    return True


def _combine(outcomes: list[PolicyOutcome]) -> PolicyOutcome:
    if not outcomes:
        return PolicyOutcome.DENY
    if PolicyOutcome.DENY in outcomes:
        return PolicyOutcome.DENY
    if PolicyOutcome.REQUIRE_HUMAN in outcomes:
        return PolicyOutcome.REQUIRE_HUMAN
    if PolicyOutcome.REQUIRE_PERMIT in outcomes:
        return PolicyOutcome.REQUIRE_PERMIT
    return PolicyOutcome.ALLOW


def evaluate_rules(request: PolicyEvaluationRequest) -> PolicyEvaluationResult:
    """Pure deterministic evaluation (no I/O)."""
    if request.decision_point not in DECISION_POINTS:
        return PolicyEvaluationResult(
            id=uuid.uuid4(),
            outcome=PolicyOutcome.DENY,
            matched_rule_ids=[],
            obligations={},
            reason_codes=["UNKNOWN_DECISION_POINT"],
            input_hash=canonical_hash(request.input_snapshot),
        )

    try:
        if not request.input_snapshot:
            return PolicyEvaluationResult(
                id=uuid.uuid4(),
                outcome=PolicyOutcome.DENY,
                matched_rule_ids=[],
                obligations={},
                reason_codes=["MISSING_INPUT_SNAPSHOT"],
                input_hash=canonical_hash({}),
            )

        # Layer by scope; DENY at any layer wins overall.
        layer_outcomes: list[PolicyOutcome] = []
        matched: list[str] = []
        obligations: dict[str, Any] = {}
        reasons: list[str] = []

        for scope in SCOPE_ORDER:
            scope_rules = [r for r in request.rules if r.scope_type == scope]
            scope_matched = [r for r in scope_rules if rule_matches(r, request)]
            if not scope_matched:
                continue
            effects = [PolicyOutcome(r.effect) for r in scope_matched]
            layer = _combine(effects)
            layer_outcomes.append(layer)
            for r in scope_matched:
                matched.append(r.id)
                obligations.update(r.obligations)
                reasons.append(f"{scope}:{r.id}:{r.effect}")

        if not layer_outcomes:
            # No matching rule → fail-closed DENY
            outcome = PolicyOutcome.DENY
            reasons.append("NO_MATCHING_RULE")
        else:
            outcome = _combine(layer_outcomes)

        return PolicyEvaluationResult(
            id=uuid.uuid4(),
            outcome=outcome,
            matched_rule_ids=matched,
            obligations=obligations,
            reason_codes=reasons,
            input_hash=canonical_hash(request.input_snapshot),
        )
    except Exception:
        return PolicyEvaluationResult(
            id=uuid.uuid4(),
            outcome=PolicyOutcome.DENY,
            matched_rule_ids=[],
            obligations={},
            reason_codes=["POLICY_EVALUATION_EXCEPTION"],
            input_hash=canonical_hash(request.input_snapshot or {}),
        )


def parse_rules(
    rules_json: list[dict[str, Any]], *, default_scope: str = "SYSTEM"
) -> list[PolicyRule]:
    rules: list[PolicyRule] = []
    for raw in rules_json:
        rules.append(
            PolicyRule(
                id=str(raw["id"]),
                decision_point=str(raw["decision_point"]),
                effect=str(raw["effect"]),
                subject=dict(raw.get("subject") or {}),
                action=dict(raw.get("action") or {}),
                resource=dict(raw.get("resource") or {}),
                obligations=dict(raw.get("obligations") or {}),
                scope_type=str(raw.get("scope_type") or default_scope),
            )
        )
    return rules


class PolicyEngine:
    def __init__(self, sessions: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sessions = sessions

    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult:
        return evaluate_rules(request)

    async def evaluate_and_persist(
        self,
        request: PolicyEvaluationRequest,
        *,
        session: AsyncSession | None = None,
        fail_closed: bool = True,
    ) -> PolicyEvaluationResult:
        result = evaluate_rules(request)
        if fail_closed and result.outcome is PolicyOutcome.DENY:
            # Persistence still occurs so auditors see the deny.
            pass
        if self._sessions is None and session is None:
            return result

        async def _write(s: AsyncSession) -> None:
            s.add(
                PolicyEvaluationModel(
                    id=result.id,
                    constitution_version_id=request.constitution_version_id,
                    decision_point=request.decision_point,
                    subject_type=request.subject_type,
                    subject_id=request.subject_id,
                    action=request.action,
                    resource=dict(request.resource),
                    input_snapshot_json=dict(request.input_snapshot),
                    input_hash=result.input_hash,
                    outcome=result.outcome.value,
                    matched_rule_ids=list(result.matched_rule_ids),
                    obligations_json=dict(result.obligations),
                    reason_codes=list(result.reason_codes),
                    evaluator_version=result.evaluator_version,
                    correlation_id=request.correlation_id or str(result.id),
                    causation_id=request.causation_id,
                )
            )

        if session is not None:
            await _write(session)
            return result
        assert self._sessions is not None
        async with self._sessions() as s, s.begin():
            await _write(s)
        return result

    def require_allow(self, result: PolicyEvaluationResult) -> None:
        if result.outcome is PolicyOutcome.DENY:
            raise DomainError(ErrorCode.POLICY_DENIED, ",".join(result.reason_codes) or "denied")
        if result.outcome is PolicyOutcome.REQUIRE_PERMIT:
            raise DomainError(ErrorCode.PERMIT_REQUIRED, "policy requires permit")
        if result.outcome is PolicyOutcome.REQUIRE_HUMAN:
            raise DomainError(ErrorCode.PERMIT_REQUIRED, "policy requires human")


def default_system_rules() -> list[PolicyRule]:
    """Bootstrap SYSTEM constitution rules for Foundation."""
    return [
        PolicyRule(
            id="system-default-allow-org-admission",
            decision_point="ORG_CANDIDATE_ADMISSION",
            effect="ALLOW",
            action={"equals": "admit_candidate"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-default-allow-org-activation",
            decision_point="ORG_ACTIVATION",
            effect="ALLOW",
            action={"equals": "activate_organization"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-deny-cross-goal",
            decision_point="A2A_DELEGATION",
            effect="DENY",
            resource={"cross_goal": True},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-mcp-readonly-allow",
            decision_point="MCP_TOOL_INVOKE",
            effect="ALLOW",
            action={"equals": "invoke"},
            resource={"side_effect_class": "NONE"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-mcp-impact-permit",
            decision_point="MCP_TOOL_INVOKE",
            effect="REQUIRE_PERMIT",
            action={"equals": "invoke"},
            resource={"risk_tier_gte": "MEDIUM"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-mcp-side-effect-permit",
            decision_point="MCP_TOOL_INVOKE",
            effect="REQUIRE_PERMIT",
            action={"equals": "invoke"},
            resource={"side_effect_class": "IRREVERSIBLE"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-mcp-reversible-permit",
            decision_point="MCP_TOOL_INVOKE",
            effect="REQUIRE_PERMIT",
            action={"equals": "invoke"},
            resource={"side_effect_class": "REVERSIBLE"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-external-effect-permit",
            decision_point="EXTERNAL_EFFECT_PREPARE",
            effect="REQUIRE_PERMIT",
            action={"equals": "prepare"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-release-require-permit",
            decision_point="RELEASE",
            effect="REQUIRE_PERMIT",
            action={"equals": "deployment.production"},
            resource={"risk_tier_gte": "HIGH"},
            obligations={"approver_role": "owner"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-agent-cert-allow",
            decision_point="AGENT_CERTIFICATION",
            effect="ALLOW",
            action={"equals": "certify"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-agent-deploy-allow",
            decision_point="AGENT_DEPLOYMENT",
            effect="ALLOW",
            action={"equals": "deploy"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-goal-confirm-allow",
            decision_point="GOAL_CONFIRM",
            effect="ALLOW",
            action={"equals": "confirm"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-memory-promotion-human",
            decision_point="MEMORY_PROMOTION",
            effect="REQUIRE_HUMAN",
            action={"equals": "promote"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-mcp-discovery-allow",
            decision_point="MCP_TOOL_DISCOVERY",
            effect="ALLOW",
            action={"equals": "discover"},
            scope_type="SYSTEM",
        ),
        PolicyRule(
            id="system-a2a-delegation-allow",
            decision_point="A2A_DELEGATION",
            effect="ALLOW",
            action={"equals": "delegate"},
            resource={"cross_goal": False},
            scope_type="SYSTEM",
        ),
    ]


def utcnow() -> datetime:
    return datetime.now(UTC)
