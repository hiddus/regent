"""Unit tests for live Preview product-surface QA."""

from __future__ import annotations

import httpx
import pytest

from regent.application.live_preview_qa import run_live_preview_qa

_SUBSTANTIAL_CSS = """
:root {
  --bg: #0f1419;
  --text: #e7ecf3;
  --accent: #3d8bfd;
}
body {
  margin: 0;
  color: var(--text);
  background: var(--bg);
  font-family: "IBM Plex Sans", "Source Han Sans SC", sans-serif;
}
.container {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 16px;
}
.card {
  display: flex;
  gap: 12px;
  padding: 16px;
}
.card:hover {
  background: #1a2332;
}
main {
  display: grid;
  gap: 16px;
}
""" + ("/* pad */\n" * 40)

_WEAK_CSS = "a{color:blue}\n"  # far below substance threshold


def _detail_html(title: str = "Article detail page with enough visible copy for QA") -> str:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><main><h1>{title}</h1>"
        f"<p>This detail page has enough body text for the live preview QA "
        f"visible-character threshold to pass under product quality gates.</p>"
        f"</main></body></html>"
    )


def _handler_ok(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/preview/runtime/d1/") or url.endswith("/preview/runtime/d1"):
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/preview/runtime/d1/static/style.css">
</head><body><main>
<article class="card"><a href="/preview/runtime/d1/item/1">Article one</a></article>
<article class="card"><a href="/preview/runtime/d1/item/2">Article two</a></article>
</main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/style.css"):
        return httpx.Response(
            200,
            text=_SUBSTANTIAL_CSS,
            headers={"content-type": "text/css"},
        )
    if "/item/" in url:
        return httpx.Response(
            200,
            text=_detail_html(),
            headers={"content-type": "text/html"},
        )
    return httpx.Response(404, text="missing")


def _handler_broken(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/preview/runtime/d2/" in url and url.rstrip("/").endswith("d2"):
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/static/style.css">
</head><body><main><a href="/item/1">A</a></main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    return httpx.Response(404, text="missing")


def _handler_empty_style(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.rstrip("/").endswith("d3"):
        home = """<!DOCTYPE html><html><head><style></style></head>
<body><main><p>x</p></main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    return httpx.Response(404, text="missing")


def _handler_weak_css(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.rstrip("/").endswith("d4"):
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/preview/runtime/d4/static/style.css">
</head><body><main>
<article class="card"><a href="/preview/runtime/d4/item/1">One</a></article>
</main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/style.css"):
        return httpx.Response(200, text=_WEAK_CSS, headers={"content-type": "text/css"})
    if "/item/" in url:
        return httpx.Response(200, text=_detail_html(), headers={"content-type": "text/html"})
    return httpx.Response(404, text="missing")


def _handler_majority_404(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.rstrip("/").endswith("d5"):
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/preview/runtime/d5/static/style.css">
</head><body><main>
<article class="card"><a href="/preview/runtime/d5/item/1">One</a></article>
<article class="card"><a href="/preview/runtime/d5/item/2">Two</a></article>
<article class="card"><a href="/preview/runtime/d5/item/3">Three</a></article>
<article class="card"><a href="/preview/runtime/d5/item/4">Four</a></article>
<article class="card"><a href="/preview/runtime/d5/item/5">Five</a></article>
</main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/style.css"):
        return httpx.Response(
            200, text=_SUBSTANTIAL_CSS, headers={"content-type": "text/css"}
        )
    if url.endswith("/item/1"):
        return httpx.Response(200, text=_detail_html(), headers={"content-type": "text/html"})
    return httpx.Response(404, text="missing")


def _handler_list_no_links(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.rstrip("/").endswith("d6"):
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/preview/runtime/d6/static/style.css">
</head><body><main>
<h1>AI 前沿情报站</h1>
<article class="card"><p>今日必读 stub without links</p></article>
<article class="card"><p>另一条资讯 stub</p></article>
</main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/style.css"):
        return httpx.Response(
            200, text=_SUBSTANTIAL_CSS, headers={"content-type": "text/css"}
        )
    return httpx.Response(404, text="missing")


@pytest.mark.asyncio
async def test_live_qa_passes_with_css_and_detail() -> None:
    transport = httpx.MockTransport(_handler_ok)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d1/", client=client
        )
    assert result.passed is True
    assert "preview-asset-reachability" not in result.failed_gap_codes()
    assert "stylesheet-substance" not in result.failed_gap_codes()


@pytest.mark.asyncio
async def test_live_qa_fails_when_css_404() -> None:
    transport = httpx.MockTransport(_handler_broken)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d2/", client=client
        )
    assert result.passed is False
    assert "preview-asset-reachability" in result.failed_gap_codes()
    assert "preview-internal-nav" in result.failed_gap_codes()


@pytest.mark.asyncio
async def test_live_qa_fails_empty_inline_style() -> None:
    transport = httpx.MockTransport(_handler_empty_style)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d3/", client=client
        )
    assert result.passed is False
    assert "stylesheet-present" in result.failed_gap_codes()


@pytest.mark.asyncio
async def test_live_qa_fails_weak_css_substance() -> None:
    transport = httpx.MockTransport(_handler_weak_css)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d4/", client=client
        )
    assert result.passed is False
    assert "stylesheet-substance" in result.failed_gap_codes()
    assert "styled-surface" in result.failed_gap_codes()


@pytest.mark.asyncio
async def test_live_qa_fails_when_majority_details_404() -> None:
    transport = httpx.MockTransport(_handler_majority_404)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d5/", client=client
        )
    assert result.passed is False
    assert "preview-internal-nav" in result.failed_gap_codes()


@pytest.mark.asyncio
async def test_live_qa_fails_list_product_without_links() -> None:
    transport = httpx.MockTransport(_handler_list_no_links)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d6/", client=client
        )
    assert result.passed is False
    assert "preview-internal-nav" in result.failed_gap_codes()


def _handler_clock_utility(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.rstrip("/").endswith("d7"):
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/preview/runtime/d7/static/styles.css">
<title>北京时间</title>
</head><body><main class="shell">
  <h1>当前北京时间</h1>
  <p class="time">12:00:00</p>
  <p>Goal 可多次修正</p>
</main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/styles.css"):
        return httpx.Response(
            200, text=_SUBSTANTIAL_CSS, headers={"content-type": "text/css"}
        )
    return httpx.Response(404, text="missing")


@pytest.mark.asyncio
async def test_live_qa_passes_utility_clock_without_list_nav() -> None:
    """Clock/time pages must not be forced through list→detail nav gates."""
    transport = httpx.MockTransport(_handler_clock_utility)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d7/", client=client
        )
    assert result.passed is True
    assert "preview-internal-nav" not in result.failed_gap_codes()
