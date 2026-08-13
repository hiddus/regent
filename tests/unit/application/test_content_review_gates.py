"""Tests for content-depth field gates and harness scoring fairness."""

from __future__ import annotations

from regent.application.harness_evolution import score_harness
from regent.application.live_preview_qa import _missing_field_groups


def test_missing_point_fields_detected() -> None:
    thin = {"title": "only title"}
    miss = _missing_field_groups(
        thin,
        (
            ("title", "name"),
            ("statute", "source"),
            ("obligations", "body"),
        ),
    )
    assert "statute|source" in miss
    assert "obligations|body" in miss


def test_score_harness_prefers_gap_coverage_over_generic_baseline() -> None:
    gaps = ["preview-content-depth"]
    baseline = (
        "# lessons\n"
        "MUST use font-family and max-width. Prefer hover states and <main>. "
        "Keep stylesheet substance and href navigation. refresh detail."
    )
    focused = (
        "# lessons\n"
        "MUST fail closed on preview-content-depth. "
        "Require /api/countries points>=10 with statute/source and "
        "/api/crosswalks steps>=10 with trigger/evidence. "
        "禁止 shell-only demos."
    )
    base_score = score_harness(gaps=gaps, lesson_text=baseline)
    cand_score = score_harness(gaps=gaps, lesson_text=focused)
    assert cand_score > base_score
