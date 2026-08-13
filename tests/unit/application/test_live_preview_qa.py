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


def _point() -> dict:
    return {
        "title": "Consent Obligation",
        "statute": "PDPA §13",
        "source": "https://example.test/pdpa",
        "obligations": (
            "Obtain informed, voluntary, and unambiguous consent before collecting, "
            "using, or disclosing personal data; provide an easy withdrawal path "
            "and cease processing after withdrawal except where law requires."
        ),
        "scenario": (
            "Collecting personal data during marketing signup, account opening, "
            "or third-party sharing workflows."
        ),
        "risk": (
            "Regulator may impose significant administrative fines and corrective "
            "orders for consent and transparency failures."
        ),
        "priority": "high",
    }


def _step() -> dict:
    return {
        "trigger": "Preparing cross-border transfer of US consumer data to Singapore processors",
        "action": (
            "Map CCPA rights to PDPA consent controls, complete a transfer impact "
            "assessment, and contract purpose limits with the recipient"
        ),
        "check": "Verify consent basis and purpose limitation are contracted with the recipient",
        "evidence": "Transfer impact assessment report and signed data processing agreement pages",
        "owner": "DPO",
        "priority": "P1",
    }


def _handler_crosswalk_depth(request: httpx.Request) -> httpx.Response:
    """Serve deep catalog only under the Preview prefix (origin /api must 404)."""
    url = str(request.url)
    prefix = "http://example.test/preview/runtime/d8"
    if url.rstrip("/") == prefix or url == prefix + "/":
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/preview/runtime/d8/static/style.css">
</head><body><main>
<h1>Crosswalk 合规对照</h1>
<p>PDPA / CCPA catalog via /api/countries and /api/crosswalks</p>
<a href="/preview/runtime/d8/countries">Countries</a>
</main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/style.css"):
        return httpx.Response(
            200, text=_SUBSTANTIAL_CSS, headers={"content-type": "text/css"}
        )
    # Origin-absolute API (the urljoin bug) must not soft-pass.
    if url == "http://example.test/api/countries" or url.startswith(
        "http://example.test/api/"
    ):
        return httpx.Response(404, text="not on preview mount")
    if url.endswith("/api/countries"):
        payload = [
            {"country_code": "SG", "points": [_point() for _ in range(10)]},
            {"country_code": "US", "points": [_point() for _ in range(10)]},
        ]
        return httpx.Response(200, json=payload)
    if "/api/crosswalks/" in url:
        return httpx.Response(
            200, json={"steps": [_step() for _ in range(10)]}
        )
    if url.endswith("/countries"):
        return httpx.Response(
            200,
            text=_detail_html("Countries catalog"),
            headers={"content-type": "text/html"},
        )
    return httpx.Response(404, text="missing")


@pytest.mark.asyncio
async def test_live_qa_content_depth_stays_under_preview_prefix() -> None:
    """Content-depth probes must not urljoin away the /preview/runtime prefix."""
    transport = httpx.MockTransport(_handler_crosswalk_depth)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_live_preview_qa(
            "http://example.test/preview/runtime/d8/", client=client
        )
    assert result.passed is True
    assert "preview-content-depth" not in result.failed_gap_codes()
    depth = next(c for c in result.checks if c.name == "preview-content-depth")
    assert depth.passed is True
    assert "SG.points=10" in depth.detail


def test_join_preview_keeps_runtime_prefix() -> None:
    from regent.application.live_preview_qa import _join_preview

    base = "http://example.test/preview/runtime/abc/"
    assert _join_preview(base, "/api/countries") == (
        "http://example.test/preview/runtime/abc/api/countries"
    )
    assert _join_preview(base.rstrip("/"), "api/crosswalks/US-SG") == (
        "http://example.test/preview/runtime/abc/api/crosswalks/US-SG"
    )
