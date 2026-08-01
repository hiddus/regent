"""Unit tests for delivery success / concurrency policy helpers."""

from __future__ import annotations

from types import SimpleNamespace

from regent.application.delivery_success_policy import (
    SAME_GAP_KIND_HARD_CAP,
    effective_max_concurrent_generating,
    verification_allows_achieve,
)


def test_effective_max_generating_auto() -> None:
    settings = SimpleNamespace(
        worker_replicas=3,
        worker_dispatch_concurrency=2,
        max_concurrent_generating=0,
    )
    assert effective_max_concurrent_generating(settings) == 12  # 3*2*2


def test_effective_max_generating_explicit_override() -> None:
    settings = SimpleNamespace(
        worker_replicas=3,
        worker_dispatch_concurrency=2,
        max_concurrent_generating=8,
    )
    # Explicit cap wins (including lower than auto) to protect fragile gateways.
    assert effective_max_concurrent_generating(settings) == 8


def test_effective_max_generating_explicit_raise() -> None:
    settings = SimpleNamespace(
        worker_replicas=3,
        worker_dispatch_concurrency=2,
        max_concurrent_generating=24,
    )
    assert effective_max_concurrent_generating(settings) == 24


def test_soft_pass_preview_allows_achieve() -> None:
    ok, reason = verification_allows_achieve(
        {"verdict": "FAIL", "gaps": ["min-visible-text"]},
        goal_scale="SMALL",
        has_preview=True,
    )
    assert ok is True
    assert reason == "soft_pass_preview"


def test_blocking_gap_still_blocks() -> None:
    ok, reason = verification_allows_achieve(
        {"verdict": "FAIL", "gaps": ["forbid-demo-shell"]},
        goal_scale="SMALL",
        has_preview=True,
    )
    assert ok is False
    assert reason == "blocking_gaps"


def test_same_gap_kind_cap_constant() -> None:
    assert SAME_GAP_KIND_HARD_CAP == 3
