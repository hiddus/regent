"""Deterministic HTML assembly from observed evidence entries."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

_BODY_RE = re.compile(r"(<body[^>]*>)(.*?)(</body>)", re.I | re.S)
_APOSTROPHE_SPACE_RE = re.compile(r"\s+([’'])")


def _clean_text(value: str) -> str:
    text = _APOSTROPHE_SPACE_RE.sub(r"\1", value.strip())
    return re.sub(r"\s+", " ", text).strip()


def _source_label(entry: dict[str, Any]) -> str:
    explicit = str(entry.get("source") or entry.get("source_name") or "").strip()
    if explicit:
        return explicit
    uri = str(entry.get("source_uri") or entry.get("link") or "").strip()
    if not uri:
        return "source"
    try:
        host = (urlparse(uri).hostname or "").lower()
    except ValueError:
        host = ""
    host = host.removeprefix("www.")
    if not host:
        return "source"
    # Prefer short publisher labels over raw feed host paths.
    known = {
        "theverge.com": "The Verge",
        "techcrunch.com": "TechCrunch",
        "hnrss.org": "Hacker News",
        "36kr.com": "36氪",
        "jiqizhixin.com": "机器之心",
        "techweb.com.cn": "TechWeb",
    }
    for domain, label in known.items():
        if host == domain or host.endswith("." + domain):
            return label
    return host


def render_observed_articles(entries: list[dict[str, Any]], *, limit: int = 20) -> str:
    blocks: list[str] = []
    for item in entries[:limit]:
        title = html.escape(_clean_text(str(item.get("title") or "Untitled") or "Untitled"))
        link = html.escape(str(item.get("link") or "#").strip() or "#")
        summary = html.escape(_clean_text(str(item.get("summary") or "")))
        source = html.escape(_source_label(item))
        blocks.append(
            "<article class=\"article\">"
            f"<span class=\"source\">{source}</span>"
            f'<a class="headline" href="{link}" target="_blank" rel="noopener noreferrer">'
            f"{title}</a>"
            f"<p class=\"summary\">{summary}</p>"
            "</article>"
        )
    return "\n".join(blocks)


def ensure_semantic_main(html_text: str) -> str:
    """Wrap primary body content in <main> when the model omitted it.

    Structural repair only — does not invent product copy. delivery-review-v1
    requires a semantic main landmark for deliverable surfaces.
    """
    if "<main" in html_text.lower():
        return html_text
    match = _BODY_RE.search(html_text)
    if match is None:
        return f"<main>\n{html_text}\n</main>"
    inner = match.group(2).strip()
    wrapped = f"{match.group(1)}\n<main>\n{inner}\n</main>\n{match.group(3)}"
    return html_text[: match.start()] + wrapped + html_text[match.end() :]


def inject_observed_entries(html_text: str, entries: list[dict[str, Any]]) -> str:
    """Replace placeholders or append an observed-entries section.

    Keeps model chrome when present, but guarantees observed headlines appear.
    """
    if not entries:
        return html_text
    articles = render_observed_articles(entries)
    markers = (
        "__ARTICLES_HTML__",
        "{{ARTICLES}}",
        "<!-- OBSERVED_ENTRIES -->",
        "<!--REGENT_OBSERVED_ENTRIES-->",
    )
    for marker in markers:
        if marker in html_text:
            return html_text.replace(marker, articles, 1)
    section = (
        '<section id="regent-observed-entries" class="articles">\n'
        "<h2>Observed headlines</h2>\n"
        f"{articles}\n"
        "</section>\n"
    )
    if "</body>" in html_text:
        return html_text.replace("</body>", section + "</body>", 1)
    return html_text + "\n" + section
