#!/usr/bin/env python3
"""CI credential scan script.

Scans the repository for plaintext credentials, secrets, and PII.
Exit code 0 = clean, exit code 1 = findings detected.

Usage:
    python scripts/credential_scan.py [directory]
    python scripts/credential_scan.py .
    python scripts/credential_scan.py core/src/regent
"""

from __future__ import annotations

import sys
from pathlib import Path

from regent.application.compliance_risk_service import ComplianceChecker, ComplianceStatus


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    if not target.is_dir():
        print(f"ERROR: {target} is not a directory", file=sys.stderr)
        return 2

    checker = ComplianceChecker()
    report = checker.scan_directory(
        target,
        exclude_patterns=[
            r"\.git",
            r"__pycache__",
            r"\.venv",
            r"node_modules",
            r"\.mypy_cache",
            r"\.pytest_cache",
            r"\.ruff_cache",
        ],
    )

    if report.status == ComplianceStatus.FAIL:
        print(f"FAIL: {len(report.findings)} critical finding(s) in {report.artifacts_scanned} file(s)")
        for finding in report.findings:
            if finding.severity.value == "CRITICAL":
                print(f"  [{finding.severity.value}] {finding.location}: {finding.message}")
        return 1
    elif report.status == ComplianceStatus.WARN:
        print(f"WARN: {len(report.findings)} high-severity finding(s) in {report.artifacts_scanned} file(s)")
        for finding in report.findings:
            print(f"  [{finding.severity.value}] {finding.location}: {finding.message}")
        return 0  # warnings don't fail CI
    else:
        print(f"PASS: {report.artifacts_scanned} file(s) scanned, no issues found")
        return 0


if __name__ == "__main__":
    sys.exit(main())
