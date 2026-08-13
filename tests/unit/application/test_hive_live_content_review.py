"""Deterministic hive live content review (no LLM / no DB)."""

from __future__ import annotations

from regent.application.generation_hive_executor import decide_live_content_review


def test_live_content_review_rejects_mechanical_failure() -> None:
    live_qa = {
        "passed": False,
        "checks": [
            {
                "name": "preview-content-depth",
                "passed": False,
                "detail": "/api/countries → 404",
            }
        ],
    }
    review = decide_live_content_review(live_qa)
    assert review["accepted"] is False
    assert "preview-content-depth" in review["gaps"]


def test_live_content_review_accepts_mechanical_pass() -> None:
    live_qa = {
        "passed": True,
        "checks": [
            {"name": "preview-content-depth", "passed": True, "detail": "US.points=12"}
        ],
    }
    review = decide_live_content_review(live_qa)
    assert review["accepted"] is True
    assert review["gaps"] == []
