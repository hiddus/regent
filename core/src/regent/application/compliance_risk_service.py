"""V3 Compliance Checker + Risk Engine.

Implements §1.3 Governance Engine components:
- ComplianceChecker: PII detection, credential scanning, security policy checks
- RiskEngine: dynamic risk identification, scoring, and escalation

Both operate as pre-execution gates: every candidate O_t and every output
artifact must pass through these before being accepted.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ComplianceStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class EscalationAction(StrEnum):
    NONE = "NONE"
    LOG = "LOG"
    REQUIRE_PERMIT = "REQUIRE_PERMIT"
    HARD_STOP = "HARD_STOP"


@dataclass(frozen=True, slots=True)
class ComplianceFinding:
    """A single compliance issue detected by the ComplianceChecker."""

    finding_id: str
    category: str  # "PII", "CREDENTIAL", "POLICY", "DATA_CLASSIFICATION"
    severity: RiskLevel
    message: str
    location: str = ""  # field name or artifact reference
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    """Result of a compliance check."""

    status: ComplianceStatus
    findings: list[ComplianceFinding] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    artifacts_scanned: int = 0

    @property
    def passed(self) -> bool:
        return self.status != ComplianceStatus.FAIL


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Risk assessment for an action or organisation candidate."""

    risk_id: str
    level: RiskLevel
    score: float  # 0.0 – 1.0
    factors: list[str] = field(default_factory=list)
    escalation: EscalationAction = EscalationAction.NONE
    rationale: str = ""
    requires_human_approval: bool = False


@dataclass(frozen=True, slots=True)
class RiskReport:
    """Aggregated risk report for a candidate O_t or action."""

    overall_level: RiskLevel
    overall_score: float
    assessments: list[RiskAssessment] = field(default_factory=list)
    escalation: EscalationAction = EscalationAction.NONE
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# ComplianceChecker
# ---------------------------------------------------------------------------

# PII patterns (simplified but effective for common cases)
_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    ),
    "phone_cn": re.compile(
        r"\b(?:(?:\+86)|(?:86))?1[3-9]\d{9}\b"
    ),
    "id_card_cn": re.compile(
        r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
    ),
    "credit_card": re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b"
    ),
    "ipv4": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    ),
}

# Credential patterns
_CREDENTIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "aws_secret_key": re.compile(
        r"""(?i)(?:aws_secret_access_key|secret_access_key)\s*[=:]\s*['"]?([A-Za-z0-9/+=]{40})['"]?"""
    ),
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"
    ),
    "generic_password": re.compile(
        r"""(?i)(?:password|passwd|pwd|secret|token|api_key|apikey)\s*[=:]\s*['"]([^\s'"]{8,})['"]"""
    ),
    "bearer_token": re.compile(
        r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"
    ),
    "connection_string": re.compile(
        r"(?i)(?:mongodb|postgres|mysql|redis)://[^\s'\"]+"
    ),
}


class ComplianceChecker:
    """Scans content for PII, credentials, and policy violations.

    All external data is treated as UNTRUSTED_DATA by default (§2.3 V3).
    """

    def __init__(self) -> None:
        self._pii_patterns = dict(_PII_PATTERNS)
        self._credential_patterns = dict(_CREDENTIAL_PATTERNS)

    def check_text(
        self,
        text: str,
        *,
        data_classification: str = "UNTRUSTED_DATA",
        context: dict[str, Any] | None = None,
    ) -> ComplianceReport:
        """Scan a text artifact for compliance issues."""
        findings: list[ComplianceFinding] = []
        ctx = context or {}

        # PII detection
        pii_findings = self._scan_pii(text)
        findings.extend(pii_findings)

        # Credential detection
        cred_findings = self._scan_credentials(text)
        findings.extend(cred_findings)

        # Data classification check
        if data_classification == "UNTRUSTED_DATA":
            classification_finding = self._check_untrusted_usage(text, ctx)
            findings.extend(classification_finding)

        # Determine overall status
        has_critical = any(f.severity == RiskLevel.CRITICAL for f in findings)
        has_high = any(f.severity == RiskLevel.HIGH for f in findings)

        if has_critical:
            status = ComplianceStatus.FAIL
        elif has_high:
            status = ComplianceStatus.WARN
        else:
            status = ComplianceStatus.PASS

        return ComplianceReport(
            status=status,
            findings=findings,
            artifacts_scanned=1,
        )

    def check_artifacts(
        self,
        artifacts: list[dict[str, str]],
        *,
        context: dict[str, Any] | None = None,
    ) -> ComplianceReport:
        """Scan multiple artifacts. Each dict must have 'content' and optional 'name'/'classification'."""
        all_findings: list[ComplianceFinding] = []
        for artifact in artifacts:
            content = artifact.get("content", "")
            classification = artifact.get("classification", "UNTRUSTED_DATA")
            sub_report = self.check_text(
                content,
                data_classification=classification,
                context=context,
            )
            all_findings.extend(sub_report.findings)

        has_critical = any(f.severity == RiskLevel.CRITICAL for f in all_findings)
        has_high = any(f.severity == RiskLevel.HIGH for f in all_findings)

        if has_critical:
            status = ComplianceStatus.FAIL
        elif has_high:
            status = ComplianceStatus.WARN
        else:
            status = ComplianceStatus.PASS

        return ComplianceReport(
            status=status,
            findings=all_findings,
            artifacts_scanned=len(artifacts),
        )

    # Binary file extensions to skip during directory scanning
    _BINARY_EXTENSIONS = frozenset({
        ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".obj", ".o",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".woff", ".woff2", ".ttf", ".eot",
        ".db", ".sqlite", ".sqlite3",
    })

    def scan_directory(
        self,
        directory: str | Path,
        *,
        max_file_size: int = 1_048_576,  # 1MB
        exclude_patterns: list[str] | None = None,
    ) -> ComplianceReport:
        """Recursively scan all text files in a directory for compliance issues.

        Skips binary files and files larger than max_file_size.
        """
        root = Path(directory)
        if not root.is_dir():
            return ComplianceReport(
                status=ComplianceStatus.FAIL,
                findings=[
                    ComplianceFinding(
                        finding_id=f"dir-not-found-{uuid.uuid4().hex[:8]}",
                        category="POLICY",
                        severity=RiskLevel.CRITICAL,
                        message=f"Directory not found: {directory}",
                    )
                ],
            )

        excludes = exclude_patterns or []
        all_findings: list[ComplianceFinding] = []
        scanned = 0

        for dirpath, dirnames, filenames in os.walk(root):
            # Skip excluded directories
            dirnames[:] = [
                d for d in dirnames
                if not any(
                    re.search(pat, d) for pat in excludes
                )
            ]

            for filename in filenames:
                filepath = Path(dirpath) / filename

                # Skip binary files
                if filepath.suffix.lower() in self._BINARY_EXTENSIONS:
                    continue

                # Skip large files
                try:
                    if filepath.stat().st_size > max_file_size:
                        continue
                except OSError:
                    continue

                # Skip excluded file patterns
                rel_path = str(filepath.relative_to(root))
                if any(re.search(pat, rel_path) for pat in excludes):
                    continue

                # Read and scan
                try:
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                except (OSError, UnicodeDecodeError):
                    continue

                sub_report = self.check_text(
                    content,
                    data_classification="UNTRUSTED_DATA",
                    context={"file": rel_path},
                )
                # Tag findings with file location
                for finding in sub_report.findings:
                    all_findings.append(
                        ComplianceFinding(
                            finding_id=finding.finding_id,
                            category=finding.category,
                            severity=finding.severity,
                            message=finding.message,
                            location=rel_path,
                            details=finding.details,
                        )
                    )
                scanned += 1

        has_critical = any(f.severity == RiskLevel.CRITICAL for f in all_findings)
        has_high = any(f.severity == RiskLevel.HIGH for f in all_findings)

        if has_critical:
            status = ComplianceStatus.FAIL
        elif has_high:
            status = ComplianceStatus.WARN
        else:
            status = ComplianceStatus.PASS

        return ComplianceReport(
            status=status,
            findings=all_findings,
            artifacts_scanned=scanned,
        )

    def _scan_pii(self, text: str) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        for kind, pattern in self._pii_patterns.items():
            matches = pattern.findall(text)
            if matches:
                findings.append(
                    ComplianceFinding(
                        finding_id=f"pii-{kind}-{uuid.uuid4().hex[:8]}",
                        category="PII",
                        severity=RiskLevel.HIGH if kind in ("id_card_cn", "credit_card") else RiskLevel.MEDIUM,
                        message=f"PII detected: {len(matches)} instance(s) of {kind}",
                        details={"type": kind, "count": len(matches)},
                    )
                )
        return findings

    def _scan_credentials(self, text: str) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        for kind, pattern in self._credential_patterns.items():
            matches = pattern.findall(text)
            if matches:
                findings.append(
                    ComplianceFinding(
                        finding_id=f"cred-{kind}-{uuid.uuid4().hex[:8]}",
                        category="CREDENTIAL",
                        severity=RiskLevel.CRITICAL,
                        message=f"credential leak detected: {kind} ({len(matches)} instance(s))",
                        details={"type": kind, "count": len(matches)},
                    )
                )
        return findings

    @staticmethod
    def _check_untrusted_usage(
        text: str,
        context: dict[str, Any],
    ) -> list[ComplianceFinding]:
        """Check if UNTRUSTED_DATA is being used as instruction or authorization."""
        findings: list[ComplianceFinding] = []
        instruction_markers = [
            "ignore previous", "override", "system prompt",
            "you are now", "new instructions", "act as",
        ]
        text_lower = text.lower()
        for marker in instruction_markers:
            if marker in text_lower:
                findings.append(
                    ComplianceFinding(
                        finding_id=f"untrusted-instruction-{uuid.uuid4().hex[:8]}",
                        category="DATA_CLASSIFICATION",
                        severity=RiskLevel.CRITICAL,
                        message=f"UNTRUSTED_DATA contains instruction-like content: '{marker}'",
                        details={"marker": marker},
                    )
                )
                break  # one finding is enough
        return findings


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------


# Risk factors and their weights
_RISK_FACTORS: dict[str, float] = {
    "external_network_access": 0.3,
    "production_deployment": 0.4,
    "financial_transaction": 0.5,
    "data_deletion": 0.3,
    "credential_usage": 0.4,
    "multi_tenant_access": 0.3,
    "irreversible_action": 0.4,
    "human_data_exposure": 0.3,
    "third_party_api_call": 0.2,
    "model_generated_code_execution": 0.3,
}


class RiskEngine:
    """Dynamic risk identification and scoring for actions and organisations.

    Every side-effect-producing action must be assessed by this engine.
    HIGH and CRITICAL risks require ExecutionPermit or human approval.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._sessions = sessions
        self._risk_factors = dict(_RISK_FACTORS)

    def assess_action(
        self,
        action_context: dict[str, Any],
        *,
        actor: str = "unknown",
    ) -> RiskAssessment:
        """Assess the risk of a single action.

        ``action_context`` should contain boolean flags for known risk factors
        (e.g. ``external_network_access=True``).
        """
        active_factors: list[str] = []
        total_score = 0.0

        for factor, weight in self._risk_factors.items():
            if action_context.get(factor, False):
                active_factors.append(factor)
                total_score += weight

        # Normalise to [0, 1]
        max_possible = sum(self._risk_factors.values())
        score = min(1.0, total_score / max_possible) if max_possible > 0 else 0.0

        # Determine level
        if score >= 0.7:
            level = RiskLevel.CRITICAL
        elif score >= 0.4:
            level = RiskLevel.HIGH
        elif score >= 0.15:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW

        # Determine escalation
        if level == RiskLevel.CRITICAL:
            escalation = EscalationAction.HARD_STOP
            requires_human = True
        elif level == RiskLevel.HIGH:
            escalation = EscalationAction.REQUIRE_PERMIT
            requires_human = True
        elif level == RiskLevel.MEDIUM:
            escalation = EscalationAction.LOG
            requires_human = False
        else:
            escalation = EscalationAction.NONE
            requires_human = False

        return RiskAssessment(
            risk_id=f"risk-{uuid.uuid4().hex[:12]}",
            level=level,
            score=round(score, 4),
            factors=active_factors,
            escalation=escalation,
            rationale=f"{len(active_factors)} risk factor(s) active; score={score:.4f}",
            requires_human_approval=requires_human,
        )

    def assess_organization(
        self,
        org_context: dict[str, Any],
    ) -> RiskReport:
        """Assess the aggregate risk of a candidate organisation O_t.

        ``org_context`` should describe the organisation's planned actions
        as boolean flags matching the risk factor keys.
        """
        assessments: list[RiskAssessment] = []

        # Assess the overall org as one action
        overall = self.assess_action(org_context, actor="org-engine")
        assessments.append(overall)

        # Assess individual roles if provided
        roles = org_context.get("roles", [])
        for role in roles:
            role_assessment = self.assess_action(
                role if isinstance(role, dict) else {},
                actor=f"role:{role.get('role', 'unknown')}" if isinstance(role, dict) else "role",
            )
            assessments.append(role_assessment)

        # Aggregate
        max_score = max((a.score for a in assessments), default=0.0)
        if max_score >= 0.7:
            overall_level = RiskLevel.CRITICAL
        elif max_score >= 0.4:
            overall_level = RiskLevel.HIGH
        elif max_score >= 0.15:
            overall_level = RiskLevel.MEDIUM
        else:
            overall_level = RiskLevel.LOW

        # Worst escalation wins
        escalation_priority = {
            EscalationAction.NONE: 0,
            EscalationAction.LOG: 1,
            EscalationAction.REQUIRE_PERMIT: 2,
            EscalationAction.HARD_STOP: 3,
        }
        worst_escalation = max(
            (a.escalation for a in assessments),
            key=lambda e: escalation_priority.get(e, 0),
        )

        return RiskReport(
            overall_level=overall_level,
            overall_score=round(max_score, 4),
            assessments=assessments,
            escalation=worst_escalation,
        )

    async def record_risk_assessment(
        self,
        goal_id: uuid.UUID,
        assessment: RiskAssessment | RiskReport,
        *,
        actor: str = "risk-engine",
    ) -> uuid.UUID:
        """Persist a risk assessment as an audit record."""
        from regent.infrastructure.models import AuditRecordModel

        if self._sessions is None:
            raise RuntimeError("RiskEngine requires sessions to persist assessments")

        audit_id = uuid.uuid4()
        if isinstance(assessment, RiskReport):
            payload = {
                "type": "RISK_REPORT",
                "overall_level": assessment.overall_level.value,
                "overall_score": assessment.overall_score,
                "escalation": assessment.escalation.value,
                "sub_assessments": [
                    {
                        "risk_id": a.risk_id,
                        "level": a.level.value,
                        "score": a.score,
                        "factors": a.factors,
                        "escalation": a.escalation.value,
                    }
                    for a in assessment.assessments
                ],
            }
        else:
            payload = {
                "type": "RISK_ASSESSMENT",
                "risk_id": assessment.risk_id,
                "level": assessment.level.value,
                "score": assessment.score,
                "factors": assessment.factors,
                "escalation": assessment.escalation.value,
                "requires_human_approval": assessment.requires_human_approval,
            }

        async with self._sessions() as session, session.begin():
            session.add(
                AuditRecordModel(
                    id=audit_id,
                    goal_id=goal_id,
                    event_type="RISK_ASSESSMENT",
                    actor=actor,
                    payload=payload,
                )
            )

        logger.info(
            "risk assessment recorded for goal %s: level=%s score=%.4f",
            goal_id,
            assessment.overall_level if isinstance(assessment, RiskReport) else assessment.level,
            assessment.overall_score if isinstance(assessment, RiskReport) else assessment.score,
        )
        return audit_id
