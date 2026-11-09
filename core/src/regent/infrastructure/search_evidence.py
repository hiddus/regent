"""Search API and web content extraction evidence connectors.

Provides:
- SearchApiEvidenceConnector: queries search engines for evidence
- WebContentExtractor: extracts clean text from HTML pages
- ResearchReportBuilder: generates structured research reports from evidence
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

from regent.application.p1_ports import EvidenceSourceRequest, EvidenceSourceSnapshot
from regent.infrastructure.artifact_store import FileArtifactStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Web content extraction (no heavy dependency required)
# ---------------------------------------------------------------------------

# Lightweight HTML-to-text extraction without trafilatura dependency.
# Strips tags, scripts, styles, and extracts readable content.
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_NAV_RE = re.compile(r"<(nav|header|footer|aside)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s{3,}")


def extract_web_content(html: str) -> dict[str, Any]:
    """Extract readable text content from HTML.

    Returns a dict with:
    - title: page title
    - text: cleaned text content
    - word_count: approximate word count
    - quality: score 0-1 based on content density
    """
    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""
    title = _TAG_RE.sub("", title).strip()[:300]

    # Remove non-content elements
    cleaned = _SCRIPT_RE.sub("", html)
    cleaned = _STYLE_RE.sub("", cleaned)
    cleaned = _COMMENT_RE.sub("", cleaned)
    cleaned = _NAV_RE.sub("", cleaned)

    # Try to find main content area
    main_patterns = [
        r"<main[^>]*>(.*?)</main>",
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*class="[^"]*(?:content|article|post|entry|main)[^"]*"[^>]*>(.*?)</div>',
        r"<body[^>]*>(.*?)</body>",
    ]
    content_html = ""
    for pattern in main_patterns:
        match = re.search(pattern, cleaned, re.DOTALL | re.IGNORECASE)
        if match:
            content_html = match.group(1)
            break

    if not content_html:
        content_html = cleaned

    # Convert block elements to newlines
    content_html = re.sub(r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", content_html, flags=re.IGNORECASE)
    # Strip remaining tags
    text = _TAG_RE.sub("", content_html)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Clean whitespace
    text = _WHITESPACE_RE.sub("\n", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    text = text[:50_000]  # Cap at 50K chars

    words = text.split()
    word_count = len(words)
    # Quality: ratio of text to total HTML size
    quality = min(1.0, word_count / 500) if word_count > 0 else 0.0

    return {
        "title": title,
        "text": text,
        "word_count": word_count,
        "quality": round(quality, 2),
    }


# ---------------------------------------------------------------------------
# Search API Evidence Connector
# ---------------------------------------------------------------------------

class SearchApiEvidenceConnector:
    """Evidence connector that queries a search API for relevant results.

    Supports any OpenAI-compatible or custom search endpoint.
    Falls back to DuckDuckGo HTML search if no API key is configured.
    """

    def __init__(
        self,
        artifacts: FileArtifactStore,
        *,
        search_api_url: str | None = None,
        search_api_key: str | None = None,
        max_results: int = 10,
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._search_api_url = search_api_url
        self._search_api_key = search_api_key
        self._max_results = max_results
        self._timeout_seconds = timeout_seconds
        self._client = client
        self.requests: list[EvidenceSourceRequest] = []

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        self.requests.append(request)
        query = request.query.strip()
        if not query:
            return []

        results = await self._search(query)
        if not results:
            return []

        snapshots: list[EvidenceSourceSnapshot] = []
        for result in results:
            snapshot = await self._store_result(request, result)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    async def _search(self, query: str) -> list[dict[str, str]]:
        """Execute search and return list of {title, url, snippet}."""
        if self._search_api_url and self._search_api_key:
            return await self._search_via_api(query)
        return await self._search_duckduckgo(query)

    async def _search_via_api(self, query: str) -> list[dict[str, str]]:
        """Search via configured API endpoint."""
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            response = await client.get(
                self._search_api_url,
                params={"q": query, "count": self._max_results},
                headers={"Authorization": f"Bearer {self._search_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            results: list[dict[str, str]] = []
            for item in data.get("results", data.get("organic_results", []))[: self._max_results]:
                results.append({
                    "title": str(item.get("title", ""))[:300],
                    "url": str(item.get("url", item.get("link", "")))[:500],
                    "snippet": str(item.get("snippet", item.get("description", "")))[:500],
                })
            return results
        except Exception:
            logger.warning("search API failed, falling back to DuckDuckGo", exc_info=True)
            return await self._search_duckduckgo(query)
        finally:
            if owns_client:
                await client.aclose()

    async def _search_duckduckgo(self, query: str) -> list[dict[str, str]]:
        """Fallback: search via DuckDuckGo HTML endpoint."""
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RegentBot/1.0)"},
        )
        try:
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
            results: list[dict[str, str]] = []
            # Parse DuckDuckGo HTML results
            result_blocks = re.findall(
                r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            )
            for href, title_html, snippet_html in result_blocks[: self._max_results]:
                title = _TAG_RE.sub("", title_html).strip()[:300]
                snippet = _TAG_RE.sub("", snippet_html).strip()[:500]
                # DuckDuckGo wraps URLs in a redirect; extract actual URL
                actual_url = href
                uddg = re.search(r"uddg=([^&]+)", href)
                if uddg:
                    from urllib.parse import unquote
                    actual_url = unquote(uddg.group(1))
                if title and actual_url:
                    results.append({"title": title, "url": actual_url[:500], "snippet": snippet})
            return results
        except Exception:
            logger.warning("DuckDuckGo search failed", exc_info=True)
            return []
        finally:
            if owns_client:
                await client.aclose()

    async def _store_result(
        self, request: EvidenceSourceRequest, result: dict[str, str]
    ) -> EvidenceSourceSnapshot | None:
        """Store a search result as an evidence snapshot."""
        url = result.get("url", "")
        if not url:
            return None
        payload = json.dumps(
            {
                "kind": "search-result",
                "title": result.get("title", ""),
                "url": url,
                "snippet": result.get("snippet", ""),
                "query": request.query,
                "correlation_id": request.correlation_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        scope = uuid.uuid5(uuid.NAMESPACE_URL, f"regent:search:{request.correlation_id}")
        stored = self._artifacts.put(scope, f"evidence/search/{digest[:2]}/{digest}.json", payload)
        return EvidenceSourceSnapshot(
            source_uri=url,
            captured_at=datetime.now(UTC).isoformat(),
            content_artifact_uri=stored.uri,
            content_hash=stored.content_hash,
            metadata={
                "connector": "search-api-v1",
                "kind": "search-result",
                "title": result.get("title", ""),
                "snippet": result.get("snippet", ""),
                "byte_size": stored.size,
                "budget": dict(request.budget),
            },
            trust_label="UNTRUSTED_DATA",
            source_type="search-result",
        )


# ---------------------------------------------------------------------------
# Enhanced HTTP connector with web content extraction
# ---------------------------------------------------------------------------

class WebContentEvidenceConnector:
    """Fetches web pages and extracts clean text content as evidence.

    Unlike AllowlistedHttpEvidenceConnector which stores raw bytes,
    this connector extracts readable text and scores quality.
    """

    def __init__(
        self,
        artifacts: FileArtifactStore,
        *,
        max_pages: int = 5,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._max_pages = max_pages
        self._timeout_seconds = timeout_seconds
        self._client = client
        self.requests: list[EvidenceSourceRequest] = []

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        self.requests.append(request)
        # Extract URLs from the query
        urls = [u.rstrip(").,;]") for u in re.findall(r"https?://[^\s<>\"']+", request.query)]
        urls = list(dict.fromkeys([*request.authorized_urls, *urls]))[: self._max_pages]
        if not urls:
            return []

        snapshots: list[EvidenceSourceSnapshot] = []
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RegentBot/1.0)"},
        )
        try:
            for url in urls:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if "html" not in content_type and "text" not in content_type:
                        continue
                    html = response.text[:200_000]
                    extracted = extract_web_content(html)
                    if extracted["word_count"] < 50:
                        continue  # Too little content
                    payload = json.dumps(extracted, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    digest = hashlib.sha256(payload).hexdigest()
                    scope = uuid.uuid5(
                        uuid.NAMESPACE_URL, f"regent:web-content:{request.correlation_id}"
                    )
                    stored = self._artifacts.put(
                        scope, f"evidence/web/{digest[:2]}/{digest}.json", payload
                    )
                    snapshots.append(
                        EvidenceSourceSnapshot(
                            source_uri=url,
                            captured_at=datetime.now(UTC).isoformat(),
                            content_artifact_uri=stored.uri,
                            content_hash=stored.content_hash,
                            metadata={
                                "connector": "web-content-v1",
                                "kind": "web-content",
                                "title": extracted["title"],
                                "word_count": extracted["word_count"],
                                "quality": extracted["quality"],
                                "byte_size": stored.size,
                                "budget": dict(request.budget),
                            },
                            trust_label="UNTRUSTED_DATA",
                            source_type="web-content",
                        )
                    )
                except Exception:
                    logger.warning("web content fetch failed", extra={"url": url}, exc_info=True)
        finally:
            if owns_client:
                await client.aclose()
        return snapshots
