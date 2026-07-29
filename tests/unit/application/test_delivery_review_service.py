"""Unit tests for delivery-review-v1 (fail-closed against demos)."""

from __future__ import annotations

import pytest
from regent.application.delivery_review_service import review_html_for_delivery
from regent.infrastructure.delivery_review_capability import (
    CAPABILITY_NAME,
    load_delivery_review_capability_package,
)


def test_capability_package_is_delivery_oriented() -> None:
    package = load_delivery_review_capability_package()
    assert package.name == CAPABILITY_NAME
    assert package.verification.get("forbid_demo_only") is True
    assert package.verification.get("fail_closed") is True
    rules = package.verification.get("rules") or {}
    assert rules.get("require_stylesheet") is True


def test_welcome_stub_is_rejected() -> None:
    html = """
    <html><head><title>Welcome</title></head>
    <body><main><h1>Welcome</h1>
    <button data-regent-event="activation">Go</button>
    </main></body></html>
    """
    result = review_html_for_delivery(html)
    assert result.passed is False
    names = {c.name: c.passed for c in result.checks}
    assert names["forbid-demo-shell"] is False or names["min-visible-text"] is False


def test_unstyled_browser_default_digest_is_rejected() -> None:
    """Screenshot-class failure: content exists, but no designed stylesheet."""
    articles = "".join(
        f"<article><span>The Verge</span>"
        f"<a href='https://example.com/{i}'>Headline {i} with enough detail for readers</a>"
        f"<p>Summary text for item {i} that fills visible content requirements.</p>"
        f"</article>"
        for i in range(1, 7)
    )
    html = f"""
    <html><head><title>Daily Tech News Digest</title></head>
    <body>
    <main>
      <h1>Daily Tech News Digest</h1>
      <button data-regent-event="activation">Refresh News</button>
      <section>{articles}</section>
    </main>
    </body></html>
    """
    result = review_html_for_delivery(html)
    assert result.passed is False
    failed = {c.name for c in result.checks if not c.passed}
    assert "stylesheet-present" in failed or "stylesheet-substance" in failed or "styled-surface" in failed


def _styled_digest_html() -> str:
    items = "".join(
        f"<article class='article'><span class='source'>36氪</span>"
        f"<a class='headline' href='https://example.com/news/{i}'>"
        f"Headline number {i} with enough detail for readers</a>"
        f"<p class='summary'>Readable summary for item {i}.</p></article>"
        for i in range(1, 8)
    )
    css = """
    :root { --ink:#1a1a1a; --paper:#f7f4ef; --accent:#0b6e4f; }
    body { margin:0; font-family: "IBM Plex Sans", sans-serif; background:var(--paper); color:var(--ink); }
    main { max-width: 720px; margin: 0 auto; padding: 2rem 1.25rem; }
    h1 { font-size: 1.75rem; margin: 0 0 1rem; }
    .articles { display: flex; flex-direction: column; gap: 1rem; }
    .article { padding: 1rem; border: 1px solid #ddd; border-radius: 8px; background: #fff; }
    .source { display:block; color: var(--accent); font-size: 0.85rem; margin-bottom: 0.35rem; }
    .headline { color: var(--ink); text-decoration: none; font-weight: 600; line-height: 1.35; }
    .summary { margin: 0.5rem 0 0; line-height: 1.5; color: #444; }
    button { margin-bottom: 1.25rem; padding: 0.55rem 0.9rem; }
    """
    return f"""
    <html><head><title>Daily Tech Digest</title><style>{css}</style></head>
    <body>
    <main>
      <h1>Daily Tech Digest</h1>
      <p>Curated headlines for operators who need a shippable briefing, not a demo shell.</p>
      <section class="articles" data-regent-list="headlines">{items}</section>
      <button data-regent-event="activation">Mark digest read</button>
    </main>
    </body></html>
    """


def test_deliverable_product_page_passes() -> None:
    result = review_html_for_delivery(_styled_digest_html())
    assert result.passed is True, result.summary + str(
        [(c.name, c.detail) for c in result.checks if not c.passed]
    )


def test_observed_entries_must_render() -> None:
    html = _styled_digest_html()
    result = review_html_for_delivery(
        html,
        acceptance_contract={
            "must_render_observed_entries": True,
            "observed_evidence_entries": [
                {"title": "Alpha Launch"},
                {"title": "Beta Ships"},
                {"title": "Gamma Update"},
            ],
        },
    )
    assert result.passed is False
    assert any(c.name == "observed-entries-rendered" and not c.passed for c in result.checks)


def test_raise_if_failed() -> None:
    result = review_html_for_delivery("<html><body>hi</body></html>")
    with pytest.raises(ValueError, match="delivery-review-v1"):
        result.raise_if_failed()


def test_unrendered_jinja_markers_are_rejected() -> None:
    html = _styled_digest_html().replace(
        "<h1>Daily Tech Digest</h1>",
        "<h1>{{ digest_title }}</h1>",
    )
    result = review_html_for_delivery(html)
    assert result.passed is False
    assert any(
        c.name == "forbid-unrendered-templates" and not c.passed for c in result.checks
    )
