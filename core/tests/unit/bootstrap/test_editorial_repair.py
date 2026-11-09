"""Unit tests for editorial repair (shadow) without model calls."""

from __future__ import annotations

import pytest

from regent.novel.application.editorial_repair import (
    AUTO_ELIGIBLE_RULES,
    locate_quote_paragraphs,
    maybe_editorial_repair,
    parse_editor_soft_strings,
    resolve_editorial_mode,
)
from regent.novel.domain.prose_patch import content_hash, split_paragraphs


def test_resolve_editorial_mode_defaults_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDITOR_REPAIR_MODE", raising=False)
    monkeypatch.delenv("REGENT_EDITOR_REPAIR_MODE", raising=False)
    from regent.config import get_settings

    get_settings.cache_clear()
    assert resolve_editorial_mode() == "shadow"
    monkeypatch.setenv("EDITOR_REPAIR_MODE", "off")
    get_settings.cache_clear()
    assert resolve_editorial_mode() == "off"
    monkeypatch.setenv("EDITOR_REPAIR_MODE", "auto")
    get_settings.cache_clear()
    assert resolve_editorial_mode() == "auto"


def test_parse_and_locate_eligible_issue() -> None:
    chapter = "第一段说明系统能抽卡。\n\n第二段又把系统能抽卡解释了一遍，显得重复。"
    base_hash = content_hash(chapter)
    soft = [
        "[editor-soft:redundant_beats] 同章信息复读｜摘录「第二段又把系统能抽卡解释了一遍」",
        "[editor-soft:contract_invisible] 契约偏弱｜摘录「没有这句话」",
    ]
    issues = parse_editor_soft_strings(soft, chapter=chapter, base_hash=base_hash)
    assert issues[0].rule_id in AUTO_ELIGIBLE_RULES
    assert issues[0].status == "eligible"
    assert issues[0].paragraph_ids
    assert issues[1].status in {"invalid", "retained"}


def test_locate_ambiguous_quote_returns_empty() -> None:
    chapter = "重复句。\n\n中间。\n\n重复句。"
    assert locate_quote_paragraphs(chapter, "重复句") == []


@pytest.mark.asyncio
async def test_shadow_without_call_skips_when_no_eligible() -> None:
    out = await maybe_editorial_repair(
        mode="shadow",
        content="短",
        soft_issues=["[editor-soft:skipped] x"],
    )
    assert out.kept_original
    assert not out.applied


def test_auto_reverts_when_fact_quote_lost() -> None:
    from regent.novel.application.chapter_accept import post_apply_safety_ok

    production = {
        "accepted": [0],
        "takes": [
            {
                "validation": {
                    "facts": [{"statement": "x", "quote": "必须保留的证据句"}],
                }
            }
        ],
    }
    ok, notes = post_apply_safety_ok(
        candidate="改写后没有那句证据了",
        production=production,
        front_fails=[],
        completion_quote="",
    )
    assert not ok
    assert any("fact_quotes_missing" in n for n in notes)


def test_auto_keeps_when_quotes_survive() -> None:
    from regent.novel.application.chapter_accept import post_apply_safety_ok

    production = {
        "accepted": [0],
        "takes": [
            {
                "validation": {
                    "facts": [{"statement": "x", "quote": "必须保留的证据句"}],
                }
            }
        ],
    }
    ok, notes = post_apply_safety_ok(
        candidate="前文。必须保留的证据句。后文微调。",
        production=production,
        front_fails=[],
        completion_quote="必须保留的证据句",
    )
    assert ok
    assert notes == []


@pytest.mark.asyncio
async def test_shadow_builds_candidate_but_does_not_apply() -> None:
    chapter = "甲段保留。\n\n乙段重复解释系统能抽卡，应当删掉重复。\n\n丙段收束。"
    base_hash = content_hash(chapter)
    soft = [
        "[editor-soft:redundant_beats] 复读｜摘录「乙段重复解释系统能抽卡，应当删掉重复。」",
    ]

    async def fake_call(schema, system, payload, purpose, command_id):
        from regent.novel.application.editorial_repair import (
            ChapterTextPatch,
            EditorialVerifyResult,
            ParagraphReplacement,
        )

        if schema is ChapterTextPatch:
            paras = split_paragraphs(chapter)
            pid = paras[1].paragraph_id
            return ChapterTextPatch(
                base_content_hash=base_hash,
                purpose="trim",
                resolved_issue_ids=[soft[0]],
                replacements=[
                    ParagraphReplacement(
                        paragraph_ids=[pid],
                        text="乙段改为：系统能抽卡。",
                    )
                ],
            )
        return EditorialVerifyResult(
            accept=True, issues_resolved=["x"], new_hard_problems=[], notes="ok"
        )

    out = await maybe_editorial_repair(
        call=fake_call,
        mode="shadow",
        content=chapter,
        soft_issues=soft,
        input_version=1,
        run_id="00000000-0000-0000-0000-000000000001",
        remaining_calls=4,
    )
    assert out.attempted
    assert out.candidate_content
    assert out.candidate_content != chapter
    assert out.applied is False
    assert out.kept_original is True
    assert "乙段改为" in (out.candidate_content or "")
