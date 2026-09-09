import json
from pathlib import Path

from regent.novel.domain.quality import ChapterEvidence, evaluate_chapters

FIXTURES = Path(__file__).parents[3] / "fixtures" / "novel_quality" / "v1"


def _load(name: str) -> list[ChapterEvidence]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [ChapterEvidence(**chapter) for chapter in data["chapters"]]


def test_repeating_story_is_rejected_with_actionable_findings() -> None:
    report = evaluate_chapters(_load("bad_repeating_story.json"))
    failed = {finding.metric for finding in report.findings if not finding.passed}
    assert not report.passed
    assert "adjacent_repetition" in failed
    assert "state_consistency" in failed
    assert "character_voice_similarity" in failed
    assert "template_phrases_per_10k_chars" in failed


def test_progressing_story_passes_deterministic_gate() -> None:
    report = evaluate_chapters(_load("good_progressing_story.json"))
    assert report.passed, report.findings


def test_human_rubric_is_versioned_and_has_release_gate() -> None:
    rubric = json.loads((FIXTURES / "human_editor_rubric.json").read_text(encoding="utf-8"))
    assert rubric["version"] == "1.0"
    assert len(rubric["dimensions"]) == 6
    assert sum(item["weight"] for item in rubric["dimensions"]) == 100
    assert "中位数>=4" in rubric["protocol"]["decision"]
