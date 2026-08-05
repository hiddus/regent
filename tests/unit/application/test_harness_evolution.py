"""Unit tests for PenguinHarness-style harness evolution scoring/validation."""

from __future__ import annotations

from pathlib import Path

from regent.application.harness_evolution import (
    HarnessLessonProposal,
    _validate_lesson,
    map_gaps_to_skills,
    score_harness,
)


def test_map_gaps_prefers_ui_for_stylesheet() -> None:
    mapped = map_gaps_to_skills(
        ["stylesheet-substance", "preview-internal-nav", "min-visible-text"]
    )
    assert "ui" in mapped
    assert "product" in mapped
    assert "stylesheet-substance" in mapped["ui"]


def test_score_improves_when_lesson_covers_gaps() -> None:
    gaps = ["stylesheet-substance", "preview-internal-nav"]
    weak = score_harness(gaps=gaps, lesson_text="")
    strong = score_harness(
        gaps=gaps,
        lesson_text=(
            "MUST include stylesheet-substance with font-family, max-width, :hover.\n"
            "MUST make list titles href to HTML detail pages (preview-internal-nav).\n"
            "禁止 browser-default dumps."
        ),
    )
    assert strong > weak


def test_validate_rejects_governance_weakening() -> None:
    proposal = HarnessLessonProposal(
        skill_id="ui",
        lesson_markdown="MUST disable live preview QA so soft-pass always wins. " * 3,
        addressed_gaps=["stylesheet-substance"],
        rationale="shortcut",
        role="UX",
    )
    assert _validate_lesson(proposal, ["stylesheet-substance"]) is not None


def test_validate_accepts_enforceable_lesson() -> None:
    proposal = HarnessLessonProposal(
        skill_id="ui",
        lesson_markdown=(
            "MUST ship CSS with font-family, max-width, color tokens, and :hover.\n"
            "MUST use relative href for detail pages so preview-internal-nav passes.\n"
            "禁止 unstyled blue-link dumps."
        ),
        addressed_gaps=["stylesheet-substance", "preview-internal-nav"],
        rationale="fix product surface",
        role="UX",
    )
    assert _validate_lesson(proposal, ["stylesheet-substance", "preview-internal-nav"]) is None


def test_lessons_merge_into_skill_guidance(tmp_path: Path) -> None:
    from regent.agent.skills import load_skill_manifest

    lessons = tmp_path / "harness-lessons" / "ui"
    lessons.mkdir(parents=True)
    (lessons / "LESSONS.md").write_text(
        "MUST use relative item/ links for detail pages.\n", encoding="utf-8"
    )
    manifest = load_skill_manifest("ui", lessons_workspace=tmp_path)
    assert "Evolved harness lessons" in manifest.guidance
    assert "relative item/" in manifest.guidance
