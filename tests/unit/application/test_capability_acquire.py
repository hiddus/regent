"""Unit tests for V3 ACQUIRE capability resolution and acquire service."""

from __future__ import annotations

import hashlib
import uuid

from regent.application.capability_acquire_service import (
    AcquireRequest,
    _check_content_hash,
    _scan_for_unsafe_patterns,
    _validate_package_manifest,
)
from regent.application.capability_ladder import (
    MAX_ATTAINMENT_ESCALATION_ATTEMPTS,
    EscalationStep,
    plan_escalation,
)
from regent.application.capability_resolution_service import (
    CapabilityGap,
    CapabilityResolutionService,
    ResolutionMethod,
)

# -- Manifest validation ------------------------------------------------------


def test_valid_manifest_passes() -> None:
    manifest = {
        "name": "my-capability-v1",
        "status": "VERIFIED",
        "description": "A test capability",
        "verification": {"protocol": "test-v1"},
    }
    ok, reason = _validate_package_manifest(manifest)
    assert ok
    assert reason == "ok"


def test_invalid_name_rejected() -> None:
    manifest = {"name": "", "status": "VERIFIED", "verification": {}}
    ok, reason = _validate_package_manifest(manifest)
    assert not ok
    assert "invalid capability name" in reason


def test_dangerous_name_rejected() -> None:
    manifest = {"name": "../../../etc/passwd", "status": "VERIFIED", "verification": {}}
    ok, _reason = _validate_package_manifest(manifest)
    assert not ok


def test_unacceptable_status_rejected() -> None:
    manifest = {"name": "ok-name", "status": "HACKED", "verification": {}}
    ok, reason = _validate_package_manifest(manifest)
    assert not ok
    assert "unacceptable status" in reason


def test_missing_verification_rejected() -> None:
    manifest = {"name": "ok-name", "status": "VERIFIED"}
    ok, reason = _validate_package_manifest(manifest)
    assert not ok
    assert "missing verification" in reason


# -- Content hash verification ------------------------------------------------


def test_hash_matches() -> None:
    content = b"test content"
    expected = hashlib.sha256(content).hexdigest()
    ok, reason = _check_content_hash(content, expected)
    assert ok
    assert reason == "hash ok"


def test_hash_mismatch() -> None:
    content = b"test content"
    ok, reason = _check_content_hash(content, "0" * 64)
    assert not ok
    assert "hash mismatch" in reason


def test_no_hash_passes() -> None:
    ok, _reason = _check_content_hash(b"anything", None)
    assert ok


# -- Safety scanning ----------------------------------------------------------


def test_clean_code_passes_scan() -> None:
    code = '''
import json
from pathlib import Path

def process(data: dict) -> str:
    return json.dumps(data)
'''
    violations = _scan_for_unsafe_patterns(code)
    assert violations == []


def test_os_system_detected() -> None:
    code = 'import os; os.system("rm -rf /")'
    violations = _scan_for_unsafe_patterns(code)
    assert any("os.system" in v for v in violations)


def test_exec_detected() -> None:
    code = 'exec("print(1)")'
    violations = _scan_for_unsafe_patterns(code)
    assert any("exec()" in v for v in violations)


def test_subprocess_detected() -> None:
    code = "import subprocess; subprocess.run(['ls'])"
    violations = _scan_for_unsafe_patterns(code)
    assert any("subprocess" in v for v in violations)


def test_socket_detected() -> None:
    code = "import socket; s = socket.socket()"
    violations = _scan_for_unsafe_patterns(code)
    assert any("socket" in v for v in violations)


# -- Resolution chain ordering ------------------------------------------------


def test_acquire_comes_after_build() -> None:
    """ACQUIRE should be selected only when build_allowed=False but acquire_allowed=True."""
    resolver = CapabilityResolutionService()
    plan = resolver.resolve(
        [
            CapabilityGap(
                requirement_key="test.acquire",
                capability_name="missing-cap",
                build_allowed=False,
                acquire_allowed=True,
                human_resolvable=True,
            )
        ],
        [],  # no existing capabilities
        [],  # no tools
    )
    assert plan.items[0].method == ResolutionMethod.ACQUIRE
    assert plan.items[0].gap_type == "ACQUIRABLE"


def test_build_preferred_over_acquire() -> None:
    """When build_allowed=True, BUILD should be chosen over ACQUIRE."""
    resolver = CapabilityResolutionService()
    plan = resolver.resolve(
        [
            CapabilityGap(
                requirement_key="test.build",
                capability_name="missing-cap",
                build_allowed=True,
                acquire_allowed=True,
                human_resolvable=True,
            )
        ],
        [],
        [],
    )
    assert plan.items[0].method == ResolutionMethod.BUILD


def test_block_when_nothing_allowed() -> None:
    """When neither build nor acquire nor human is allowed, should BLOCK."""
    resolver = CapabilityResolutionService()
    plan = resolver.resolve(
        [
            CapabilityGap(
                requirement_key="test.block",
                capability_name="missing-cap",
                build_allowed=False,
                acquire_allowed=False,
                human_resolvable=False,
            )
        ],
        [],
        [],
    )
    assert plan.items[0].method == ResolutionMethod.BLOCK


# -- Ladder escalation --------------------------------------------------------


def test_ladder_includes_acquire() -> None:
    """The escalation ladder should include ACQUIRE after BUILD."""
    assert EscalationStep.ACQUIRE in EscalationStep
    steps = [
        EscalationStep.REUSE, EscalationStep.COMPOSE,
        EscalationStep.BUILD, EscalationStep.ACQUIRE,
    ]
    for i, step in enumerate(steps):
        plan = plan_escalation(i)
        assert plan.step == step
        assert not plan.exhausted


def test_ladder_exhausted_after_acquire() -> None:
    """After ACQUIRE attempt, the ladder should be exhausted."""
    plan = plan_escalation(MAX_ATTAINMENT_ESCALATION_ATTEMPTS)
    assert plan.exhausted
    assert plan.step == EscalationStep.STOP


def test_acquire_is_fourth_step() -> None:
    """ACQUIRE should be the 4th escalation step (index 3)."""
    plan = plan_escalation(3)
    assert plan.step == EscalationStep.ACQUIRE
    assert plan.attempt == 4
    assert not plan.exhausted


# -- AcquireRequest construction -----------------------------------------------


def test_acquire_request_has_required_fields() -> None:
    goal_id = uuid.uuid4()
    req = AcquireRequest(
        capability_name="test-cap",
        requirement_key="test.key",
        goal_id=goal_id,
    )
    assert req.capability_name == "test-cap"
    assert req.goal_id == goal_id
    assert req.authorized_urls == ()


def test_acquire_request_with_authorized_urls() -> None:
    goal_id = uuid.uuid4()
    req = AcquireRequest(
        capability_name="test-cap",
        requirement_key="test.key",
        goal_id=goal_id,
        authorized_urls=("https://example.com/capabilities",),
    )
    assert len(req.authorized_urls) == 1
