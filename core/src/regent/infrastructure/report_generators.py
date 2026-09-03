"""Multi-format report and artifact generators.

Produces Markdown reports, styled HTML reports, and CSV/Excel spreadsheets
from structured data. These extend the generation pipeline beyond HTML apps.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from regent.infrastructure.artifact_store import FileArtifactStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

class ReportSection:
    """A single section of a report."""

    __slots__ = ("title", "body", "tables", "metadata")

    def __init__(
        self,
        title: str,
        body: str = "",
        tables: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.title = title
        self.body = body
        self.tables = tables or []
        self.metadata = metadata or {}


class ReportPayload:
    """Complete report input for all generators."""

    __slots__ = ("title", "subtitle", "author", "sections", "metadata")

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        author: str = "Regent Core",
        sections: list[ReportSection] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.title = title
        self.subtitle = subtitle
        self.author = author
        self.sections = sections or []
        self.metadata = metadata or {}


class GeneratedArtifact:
    """Output from any generator."""

    __slots__ = ("filename", "content", "media_type", "content_hash", "size")

    def __init__(self, filename: str, content: bytes, media_type: str) -> None:
        self.filename = filename
        self.content = content
        self.media_type = media_type
        self.content_hash = hashlib.sha256(content).hexdigest()
        self.size = len(content)


# ---------------------------------------------------------------------------
# 2.1 Markdown Report Generator
# ---------------------------------------------------------------------------

class MarkdownReportGenerator:
    """Generate a Markdown report from a ReportPayload."""

    def generate(self, payload: ReportPayload) -> GeneratedArtifact:
        lines: list[str] = []
        lines.append(f"# {payload.title}")
        if payload.subtitle:
            lines.append(f"\n*{payload.subtitle}*")
        lines.append(f"\n> Author: {payload.author}  ")
        lines.append(f"> Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("")

        # Table of contents
        if len(payload.sections) > 1:
            lines.append("## Table of Contents\n")
            for i, section in enumerate(payload.sections, 1):
                anchor = section.title.lower().replace(" ", "-")
                lines.append(f"{i}. [{section.title}](#{anchor})")
            lines.append("")

        for section in payload.sections:
            lines.append(f"## {section.title}\n")
            if section.body:
                lines.append(section.body)
                lines.append("")
            for table in section.tables:
                lines.extend(_render_md_table(table))
                lines.append("")
            if section.metadata:
                lines.append("<details>")
                lines.append("<summary>Metadata</summary>\n")
                lines.append("```json")
                lines.append(json.dumps(section.metadata, indent=2, ensure_ascii=False))
                lines.append("```")
                lines.append("</details>")
                lines.append("")

        if payload.metadata:
            lines.append("---")
            lines.append("\n### Report Metadata\n")
            lines.append("```json")
            lines.append(json.dumps(payload.metadata, indent=2, ensure_ascii=False))
            lines.append("```")

        content = "\n".join(lines).encode("utf-8")
        safe_title = payload.title.lower().replace(" ", "_")[:40]
        return GeneratedArtifact(f"{safe_title}.md", content, "text/markdown")


def _render_md_table(data: dict[str, Any]) -> list[str]:
    """Render a dict with 'columns' (list[str]) and 'rows' (list[list]) as MD table."""
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns:
        return []
    lines = ["| " + " | ".join(str(c) for c in columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        cells = [str(cell).replace("|", "\\|") for cell in row]
        while len(cells) < len(columns):
            cells.append("")
        lines.append("| " + " | ".join(cells[: len(columns)]) + " |")
    return lines


# ---------------------------------------------------------------------------
# 2.2 HTML Report Generator
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#fff;--text:#1a1a2e;--muted:#6b7280;--accent:#2563eb;--border:#e5e7eb;--code-bg:#f3f4f6;--table-stripe:#f9fafb}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text);background:var(--bg);max-width:860px;margin:0 auto;padding:40px 24px}}
h1{{font-size:28px;margin-bottom:8px;color:var(--accent)}}
h2{{font-size:20px;margin:32px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--border)}}
h3{{font-size:16px;margin:20px 0 8px}}
.subtitle{{color:var(--muted);font-style:italic;margin-bottom:4px}}
.meta{{color:var(--muted);font-size:13px;margin-bottom:24px}}
p{{margin-bottom:12px}}
table{{border-collapse:collapse;width:100%;margin:12px 0 20px;font-size:14px}}
th,td{{border:1px solid var(--border);padding:8px 12px;text-align:left}}
th{{background:var(--accent);color:#fff;font-weight:600}}
tr:nth-child(even){{background:var(--table-stripe)}}
code{{background:var(--code-bg);padding:2px 6px;border-radius:4px;font-size:13px}}
pre{{background:var(--code-bg);padding:16px;border-radius:8px;overflow-x:auto;margin:12px 0}}
pre code{{background:none;padding:0}}
details{{margin:8px 0;padding:8px 12px;border:1px solid var(--border);border-radius:8px}}
summary{{cursor:pointer;font-weight:600;color:var(--muted)}}
.toc{{background:var(--code-bg);padding:16px 20px;border-radius:10px;margin:16px 0 24px}}
.toc ol{{padding-left:20px}}
.toc a{{color:var(--accent);text-decoration:none}}
.toc a:hover{{text-decoration:underline}}
.footer{{margin-top:40px;padding-top:16px;border-top:1px solid var(--border);color:var(--muted);font-size:12px}}
@media print{{body{{max-width:none;padding:20px}}}}
</style>
</head>
<body>
<h1>{title}</h1>
{subtitle_html}
<div class="meta">Author: {author} &middot; Generated: {date}</div>
{toc_html}
{sections_html}
<div class="footer">Generated by Regent Core</div>
</body>
</html>
"""


class HTMLReportGenerator:
    """Generate a styled HTML report from a ReportPayload."""

    def generate(self, payload: ReportPayload) -> GeneratedArtifact:
        subtitle_html = (
            f'<p class="subtitle">{_esc(payload.subtitle)}</p>' if payload.subtitle else ""
        )
        date_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        # TOC
        toc_html = ""
        if len(payload.sections) > 1:
            items = []
            for i, section in enumerate(payload.sections, 1):
                anchor = _slugify(section.title)
                items.append(f'<li><a href="#{anchor}">{_esc(section.title)}</a></li>')
            toc_html = f'<nav class="toc"><strong>Table of Contents</strong><ol>{"".join(items)}</ol></nav>'

        sections_html_parts: list[str] = []
        for section in payload.sections:
            anchor = _slugify(section.title)
            parts = [f'<h2 id="{anchor}">{_esc(section.title)}</h2>']
            if section.body:
                parts.append(f"<p>{_esc(section.body)}</p>")
            for table in section.tables:
                parts.append(_render_html_table(table))
            if section.metadata:
                parts.append("<details><summary>Metadata</summary>")
                parts.append(f"<pre><code>{_esc(json.dumps(section.metadata, indent=2, ensure_ascii=False))}</code></pre>")
                parts.append("</details>")
            sections_html_parts.append("\n".join(parts))

        html = _HTML_TEMPLATE.format(
            title=_esc(payload.title),
            subtitle_html=subtitle_html,
            author=_esc(payload.author),
            date=date_str,
            toc_html=toc_html,
            sections_html="\n".join(sections_html_parts),
        )
        content = html.encode("utf-8")
        safe_title = payload.title.lower().replace(" ", "_")[:40]
        return GeneratedArtifact(f"{safe_title}.html", content, "text/html")


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _slugify(text: str) -> str:
    return text.lower().replace(" ", "-").replace("_", "-")


def _render_html_table(data: dict[str, Any]) -> str:
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns:
        return ""
    parts = ["<table><thead><tr>"]
    for col in columns:
        parts.append(f"<th>{_esc(str(col))}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for i, cell in enumerate(row):
            val = str(cell) if i < len(row) else ""
            parts.append(f"<td>{_esc(val)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 2.3 CSV / Spreadsheet Generator
# ---------------------------------------------------------------------------

class SpreadsheetGenerator:
    """Generate CSV or Excel-compatible output from tabular data."""

    def generate_csv(self, payload: dict[str, Any]) -> GeneratedArtifact:
        columns = payload.get("columns", [])
        rows = payload.get("rows", [])
        title = payload.get("title", "data")
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        writer.writerows(rows)
        content = buf.getvalue().encode("utf-8")
        safe_title = title.lower().replace(" ", "_")[:40]
        return GeneratedArtifact(f"{safe_title}.csv", content, "text/csv")

    def generate_html_table(self, payload: dict[str, Any]) -> GeneratedArtifact:
        """Generate a standalone HTML file with a styled data table."""
        columns = payload.get("columns", [])
        rows = payload.get("rows", [])
        title = payload.get("title", "Data Table")
        table_html = _render_html_table({"columns": columns, "rows": rows})
        html = f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>
body{{font:14px/1.6 system-ui,sans-serif;max-width:960px;margin:0 auto;padding:24px}}
h1{{font-size:22px;margin-bottom:16px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #e5e7eb;padding:8px 12px;text-align:left}}
th{{background:#2563eb;color:#fff}}
tr:nth-child(even){{background:#f9fafb}}
.count{{color:#6b7280;font-size:13px;margin-bottom:12px}}
</style>
</head>
<body>
<h1>{_esc(title)}</h1>
<p class="count">{len(rows)} rows, {len(columns)} columns</p>
{table_html}
</body>
</html>"""
        safe_title = title.lower().replace(" ", "_")[:40]
        return GeneratedArtifact(
            f"{safe_title}.html", html.encode("utf-8"), "text/html"
        )


# ---------------------------------------------------------------------------
# Artifact store integration
# ---------------------------------------------------------------------------

class MultiFormatArtifactPublisher:
    """Generate reports in multiple formats and store as artifacts."""

    def __init__(self, artifacts: FileArtifactStore) -> None:
        self._artifacts = artifacts
        self._md = MarkdownReportGenerator()
        self._html = HTMLReportGenerator()
        self._spreadsheet = SpreadsheetGenerator()

    def publish_report(
        self,
        payload: ReportPayload,
        *,
        formats: list[str] | None = None,
    ) -> list[GeneratedArtifact]:
        """Generate and store a report in requested formats (default: all)."""
        formats = formats or ["markdown", "html"]
        results: list[GeneratedArtifact] = []
        scope = uuid.uuid5(
            uuid.NAMESPACE_URL, f"regent:report:{payload.title}:{datetime.now(UTC).isoformat()}"
        )
        if "markdown" in formats:
            artifact = self._md.generate(payload)
            self._artifacts.put(scope, f"reports/{artifact.filename}", artifact.content)
            results.append(artifact)
        if "html" in formats:
            artifact = self._html.generate(payload)
            self._artifacts.put(scope, f"reports/{artifact.filename}", artifact.content)
            results.append(artifact)
        return results

    def publish_spreadsheet(
        self,
        payload: dict[str, Any],
        *,
        fmt: str = "csv",
    ) -> GeneratedArtifact:
        scope = uuid.uuid5(
            uuid.NAMESPACE_URL, f"regent:spreadsheet:{payload.get('title', 'data')}"
        )
        if fmt == "html":
            artifact = self._spreadsheet.generate_html_table(payload)
        else:
            artifact = self._spreadsheet.generate_csv(payload)
        self._artifacts.put(scope, f"spreadsheets/{artifact.filename}", artifact.content)
        return artifact
