"""Rewrite root-absolute asset/nav URLs for path-prefixed runtime Preview.

Apps commonly emit ``href="/static/..."`` and ``href="/item/1"``. Under
``/preview/runtime/{id}/`` those resolve to the API host root and break CSS,
detail pages, and forms. Root-relative paths ignore ``<base href>``, so the
proxy must rewrite them.
"""

from __future__ import annotations

import re
from typing import Final

# href="/x", src='/x', action="/x", poster="/x", formaction="/x", data-src="/x"
_ATTR_ROOT_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?P<prefix>\b(?:href|src|action|poster|formaction|data-src|data-href)\s*=\s*)"""
    r"""(?P<quote>["'])"""
    r"""/(?!/)(?P<path>[^"']*)"""
    r"""(?P=quote)""",
    re.IGNORECASE,
)

# CSS url(/static/...) — not url(//cdn) or url(http...)
_CSS_URL_ROOT_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?P<prefix>\burl\(\s*)(?P<quote>["']?)/(?!/)(?P<path>[^)"']*)(?P=quote)\s*\)""",
    re.IGNORECASE,
)

_BASE_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"<base\b[^>]*>", re.IGNORECASE
)
_HEAD_OPEN_RE: Final[re.Pattern[str]] = re.compile(
    r"<head\b[^>]*>", re.IGNORECASE
)


def preview_public_prefix(deployment_id: str) -> str:
    did = str(deployment_id or "").strip().strip("/")
    return f"/preview/runtime/{did}/"


def rewrite_root_absolute_urls(text: str, *, public_prefix: str) -> str:
    """Map ``/path`` → ``{public_prefix}path`` inside HTML/CSS text."""
    prefix = public_prefix if public_prefix.endswith("/") else f"{public_prefix}/"

    def _attr(match: re.Match[str]) -> str:
        path = match.group("path")
        return f"{match.group('prefix')}{match.group('quote')}{prefix}{path}{match.group('quote')}"

    def _css(match: re.Match[str]) -> str:
        path = match.group("path")
        quote = match.group("quote") or ""
        return f"{match.group('prefix')}{quote}{prefix}{path}{quote})"

    out = _ATTR_ROOT_RE.sub(_attr, text)
    return _CSS_URL_ROOT_RE.sub(_css, out)


def inject_base_href(html: str, *, public_prefix: str) -> str:
    """Ensure a single ``<base href="{prefix}">`` exists (defense in depth)."""
    prefix = public_prefix if public_prefix.endswith("/") else f"{public_prefix}/"
    base_tag = f'<base href="{prefix}">'
    if _BASE_TAG_RE.search(html):
        return _BASE_TAG_RE.sub(base_tag, html, count=1)
    head = _HEAD_OPEN_RE.search(html)
    if head is None:
        return html
    insert_at = head.end()
    return html[:insert_at] + "\n  " + base_tag + html[insert_at:]


def rewrite_preview_html(html: str, *, deployment_id: str) -> str:
    prefix = preview_public_prefix(deployment_id)
    rewritten = rewrite_root_absolute_urls(html, public_prefix=prefix)
    return inject_base_href(rewritten, public_prefix=prefix)


def rewrite_preview_css(css: str, *, deployment_id: str) -> str:
    return rewrite_root_absolute_urls(
        css, public_prefix=preview_public_prefix(deployment_id)
    )


def rewrite_location_header(location: str, *, deployment_id: str) -> str:
    """Rewrite absolute-path Location redirects onto the preview prefix."""
    loc = (location or "").strip()
    if not loc.startswith("/") or loc.startswith("//"):
        return loc
    prefix = preview_public_prefix(deployment_id).rstrip("/")
    if loc == prefix or loc.startswith(prefix + "/"):
        return loc
    return f"{prefix}{loc}"
