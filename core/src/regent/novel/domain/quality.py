"""Deterministic quality signals for multi-chapter fiction.

These checks deliberately cover observable failure modes.  They do not claim to
measure literary merit; that remains a blinded editorial review (see the fixture).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

DEFAULT_TEMPLATE_PHRASES = (
    "瞳孔骤缩",
    "不再犹豫",
    "倒吸一口凉气",
    "嘴角勾起一抹",
    "如山如岳",
    "咬破舌尖",
    "眼中闪过一丝",
    "剑尖锁定后心",
)


@dataclass(frozen=True)
class ChapterEvidence:
    chapter_no: int
    text: str
    state_before: Mapping[str, str] = field(default_factory=dict)
    state_after: Mapping[str, str] = field(default_factory=dict)
    # Dialogue grouped by speaker. Supplying attributed dialogue makes the voice
    # metric auditable instead of asking heuristics to guess the speaker.
    dialogue_by_character: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityFinding:
    metric: str
    score: float
    passed: bool
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    findings: tuple[QualityFinding, ...]


def _normalise(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def _sentences(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split("[。\\uFF01\\uFF1F!?\\n]+", value)
        if len(_normalise(part)) >= 8
    ]


def _ngrams(value: str, size: int = 5) -> set[str]:
    value = _normalise(value)
    return {value[index : index + size] for index in range(max(0, len(value) - size + 1))}


def adjacent_repetition(previous: str, current: str) -> QualityFinding:
    """Combine exact sentence reuse and five-character n-gram overlap."""
    left, right = (
        {_normalise(s) for s in _sentences(previous)},
        {_normalise(s) for s in _sentences(current)},
    )
    exact = len(left & right) / max(1, min(len(left), len(right)))
    left_grams, right_grams = _ngrams(previous), _ngrams(current)
    jaccard = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    score = max(exact, jaccard)
    repeats = tuple(s for s in _sentences(current) if _normalise(s) in left)[:5]
    return QualityFinding(
        "adjacent_repetition", round(score, 4), exact <= 0.12 and jaccard <= 0.30, repeats
    )


def net_progression(chapter: ChapterEvidence) -> QualityFinding:
    """Require durable state change; prose length is never accepted as progress."""
    keys = set(chapter.state_before) | set(chapter.state_after)
    changed = tuple(
        f"{key}: {chapter.state_before.get(key)!r} -> {chapter.state_after.get(key)!r}"
        for key in sorted(keys)
        if chapter.state_before.get(key) != chapter.state_after.get(key)
    )
    # Two changes prevents a cosmetic location-only change from passing a chapter.
    return QualityFinding("net_progression", float(len(changed)), len(changed) >= 2, changed[:8])


def state_consistency(chapters: Sequence[ChapterEvidence]) -> QualityFinding:
    conflicts: list[str] = []
    prior: Mapping[str, str] | None = None
    for chapter in chapters:
        if prior is not None:
            for key in set(prior) & set(chapter.state_before):
                if prior[key] != chapter.state_before[key]:
                    actual = chapter.state_before[key]
                    conflicts.append(
                        f"chapter {chapter.chapter_no} {key}: "
                        f"expected {prior[key]!r}, got {actual!r}"
                    )
        prior = chapter.state_after
    return QualityFinding(
        "state_consistency", float(len(conflicts)), not conflicts, tuple(conflicts[:8])
    )


def _cosine(left: str, right: str) -> float:
    a, b = Counter(_ngrams(left, 2)), Counter(_ngrams(right, 2))
    dot = sum(value * b[key] for key, value in a.items())
    norm = math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
    return dot / norm if norm else 1.0


def character_voice(chapters: Sequence[ChapterEvidence]) -> QualityFinding:
    corpus: dict[str, list[str]] = {}
    for chapter in chapters:
        for character, lines in chapter.dialogue_by_character.items():
            corpus.setdefault(character, []).extend(lines)
    usable = {
        name: "".join(lines)
        for name, lines in corpus.items()
        if len(_normalise("".join(lines))) >= 20
    }
    similarities: list[tuple[str, float]] = []
    names = sorted(usable)
    for index, name in enumerate(names):
        for other in names[index + 1 :]:
            similarities.append((f"{name}/{other}", _cosine(usable[name], usable[other])))
    worst = max((score for _, score in similarities), default=1.0)
    evidence = tuple(
        f"{pair}: {score:.3f}" for pair, score in sorted(similarities, key=lambda x: -x[1])[:5]
    )
    return QualityFinding(
        "character_voice_similarity", round(worst, 4), len(usable) >= 2 and worst <= 0.72, evidence
    )


def template_density(
    chapters: Sequence[ChapterEvidence], phrases: Sequence[str] = DEFAULT_TEMPLATE_PHRASES
) -> QualityFinding:
    text = "".join(chapter.text for chapter in chapters)
    hits = [(phrase, text.count(phrase)) for phrase in phrases if text.count(phrase)]
    density = sum(count for _, count in hits) * 10_000 / max(1, len(_normalise(text)))
    return QualityFinding(
        "template_phrases_per_10k_chars",
        round(density, 3),
        density <= 3.0,
        tuple(f"{phrase}: {count}" for phrase, count in hits),
    )


def evaluate_chapters(chapters: Sequence[ChapterEvidence]) -> QualityReport:
    if not chapters:
        raise ValueError("at least one chapter is required")
    findings: list[QualityFinding] = [net_progression(chapter) for chapter in chapters]
    findings.extend(
        adjacent_repetition(left.text, right.text) for left, right in pairwise(chapters)
    )
    findings.extend(
        (state_consistency(chapters), character_voice(chapters), template_density(chapters))
    )
    return QualityReport(all(item.passed for item in findings), tuple(findings))
