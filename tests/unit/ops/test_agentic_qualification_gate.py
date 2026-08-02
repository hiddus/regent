"""Tests for ops qualification ladder gate (adjacent + report)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

_GATE_PATH = Path(__file__).resolve().parents[3] / "ops" / "agentic_qualification_gate.py"
_spec = importlib.util.spec_from_file_location("agentic_qualification_gate", _GATE_PATH)
assert _spec and _spec.loader
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)

is_adjacent_or_downgrade = _gate.is_adjacent_or_downgrade
report_has_live_model_v2 = _gate.report_has_live_model_v2
validate_promotion = _gate.validate_promotion


def test_adjacent_and_downgrade() -> None:
    assert is_adjacent_or_downgrade(current="DISABLED", target="OFFLINE_QUALIFICATION")
    assert not is_adjacent_or_downgrade(current="DISABLED", target="DEFAULT")
    assert is_adjacent_or_downgrade(current="CANARY_50", target="DISABLED")


def test_fixture_report_blocks_dogfood() -> None:
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "gate": {
            "allows_state": "OFFLINE_QUALIFICATION",
            "live_model_v2_green": False,
        },
        "checks": [{"id": "fixture_golden_preview", "ok": True}],
    }
    assert report_has_live_model_v2(report) is False
    errs = validate_promotion(
        current="OFFLINE_QUALIFICATION",
        target="INTERNAL_DOGFOOD",
        report=report,
    )
    assert any("live-model" in e for e in errs)


def test_offline_upgrade_with_fresh_report() -> None:
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "gate": {"allows_state": "OFFLINE_QUALIFICATION"},
        "checks": [],
    }
    assert (
        validate_promotion(
            current="DISABLED",
            target="OFFLINE_QUALIFICATION",
            report=report,
        )
        == []
    )


def test_stale_report_denied() -> None:
    report = {
        "created_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
        "gate": {"allows_state": "OFFLINE_QUALIFICATION"},
    }
    errs = validate_promotion(
        current="DISABLED",
        target="OFFLINE_QUALIFICATION",
        report=report,
    )
    assert any("stale" in e for e in errs)


def test_force_allows_jump() -> None:
    assert (
        validate_promotion(
            current="DISABLED",
            target="DEFAULT",
            report=None,
            force=True,
        )
        == []
    )


def test_dogfood_with_live_v2() -> None:
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "gate": {
            "allows_state": "INTERNAL_DOGFOOD",
            "live_model_v2_green": True,
        },
        "checks": [{"id": "live_model_revise_v2", "ok": True}],
    }
    assert (
        validate_promotion(
            current="OFFLINE_QUALIFICATION",
            target="INTERNAL_DOGFOOD",
            report=report,
        )
        == []
    )


def test_canary_requires_sample_gates() -> None:
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "gate": {
            "allows_state": "CANARY_5",
            "live_model_v2_green": True,
        },
        "checks": [{"id": "live_model_revise_v2", "ok": True}],
    }
    errs = validate_promotion(
        current="INTERNAL_DOGFOOD",
        target="CANARY_5",
        report=report,
    )
    assert any("sample_gates" in e for e in errs)
    report["sample_gates"] = {
        "dogfood_independent_tasks": 20,
        "infra_false_fail_count": 0,
    }
    assert (
        validate_promotion(
            current="INTERNAL_DOGFOOD",
            target="CANARY_5",
            report=report,
        )
        == []
    )
