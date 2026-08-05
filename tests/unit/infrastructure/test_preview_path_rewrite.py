"""Unit tests for Preview path-prefix URL rewriting."""

from __future__ import annotations

from regent.infrastructure.preview_path_rewrite import (
    inject_base_href,
    preview_public_prefix,
    rewrite_location_header,
    rewrite_preview_css,
    rewrite_preview_html,
    rewrite_root_absolute_urls,
)


def test_preview_public_prefix() -> None:
    assert preview_public_prefix("abc") == "/preview/runtime/abc/"


def test_rewrite_html_root_absolute_and_base() -> None:
    html = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/static/style.css">
</head><body>
<main><a href="/item/seed-001">t</a><a href="https://example.com/x">ext</a></main>
</body></html>"""
    out = rewrite_preview_html(html, deployment_id="dep-1")
    assert 'href="/preview/runtime/dep-1/static/style.css"' in out
    assert 'href="/preview/runtime/dep-1/item/seed-001"' in out
    assert 'href="https://example.com/x"' in out
    assert '<base href="/preview/runtime/dep-1/">' in out
    # Protocol-relative untouched by attr rewriter (leading //).
    assert "https://example.com/x" in out


def test_rewrite_skips_protocol_relative() -> None:
    html = '<a href="//cdn.example/a.js">x</a>'
    out = rewrite_root_absolute_urls(html, public_prefix="/preview/runtime/d/")
    assert 'href="//cdn.example/a.js"' in out


def test_rewrite_css_urls() -> None:
    css = "body{background:url(/static/bg.png)} .x{background:url('https://x/a.png')}"
    out = rewrite_preview_css(css, deployment_id="d2")
    assert "url(/preview/runtime/d2/static/bg.png)" in out
    assert "url('https://x/a.png')" in out


def test_rewrite_location_header() -> None:
    assert (
        rewrite_location_header("/item/1", deployment_id="z")
        == "/preview/runtime/z/item/1"
    )
    assert (
        rewrite_location_header("/preview/runtime/z/item/1", deployment_id="z")
        == "/preview/runtime/z/item/1"
    )
    assert rewrite_location_header("https://x/y", deployment_id="z") == "https://x/y"


def test_inject_base_replaces_existing() -> None:
    html = "<head><base href='/old/'></head>"
    out = inject_base_href(html, public_prefix="/preview/runtime/n/")
    assert out.count("<base") == 1
    assert 'href="/preview/runtime/n/"' in out
