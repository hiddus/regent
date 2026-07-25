"""Unit tests for P2-2/3/4 services (pure logic + state gates)."""

from __future__ import annotations

from regent.application.eval_harness_service import POLICY_VERSION
from regent.application.p1_contracts import canonical_hash
from regent.application.runtime_profile_service import BOOTSTRAP_PROFILES


def test_bootstrap_profiles_include_certified_pair() -> None:
    certified = {p["name"] for p in BOOTSTRAP_PROFILES if p["status"] == "CERTIFIED"}
    assert certified == {"python-web-v1", "static-web-v1"}
    draft = {p["name"] for p in BOOTSTRAP_PROFILES if p["status"] == "DRAFT"}
    assert "node-web-v1" in draft
    assert "python-data-v1" in draft


def test_eval_policy_version_frozen() -> None:
    assert POLICY_VERSION == "eval-harness-v1"


def test_eval_stub_scoring_is_deterministic() -> None:
    seed = "s1"
    tid = "t1"
    a = canonical_hash({"seed": seed, "task": tid})
    b = canonical_hash({"seed": seed, "task": tid})
    assert a == b
    pass_at_1 = int(a[:2], 16) % 2 == 0
    assert isinstance(pass_at_1, bool)
