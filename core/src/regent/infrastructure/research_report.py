"""Research report generation from evidence snapshots.

Transforms raw evidence (search results, web content, intent data) into
structured research reports in Markdown and HTML formats.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from regent.application.p1_ports import EvidenceSourceSnapshot
from regent.infrastructure.report_generators import (
    HTMLReportGenerator,
    MarkdownReportGenerator,
    ReportPayload,
    ReportSection,
    GeneratedArtifact,
)

logger = logging.getLogger(__name__)


class ResearchReportBuilder:
    """Build structured research reports from evidence snapshots."""

    def __init__(self, *, max_evidence_per_section: int = 20) -> None:
        self._max_per_section = max_evidence_per_section

    def build(
        self,
        *,
        query: str,
        snapshots: list[EvidenceSourceSnapshot],
        extra_sections: list[dict[str, Any]] | None = None,
    ) -> ReportPayload:
        """Build a ReportPayload from evidence snapshots."""
        sections: list[ReportSection] = []

        # Executive summary
        sections.append(self._build_summary_section(query, snapshots))

        # Evidence by source type
        by_connector: dict[str, list[EvidenceSourceSnapshot]] = {}
        for snap in snapshots:
            connector = snap.metadata.get("connector", "unknown")
            by_connector.setdefault(connector, []).append(snap)

        for connector_name, items in by_connector.items():
            section = self._build_evidence_section(connector_name, items)
            if section:
                sections.append(section)

        # Search results section
        search_items = [
            s for s in snapshots if s.metadata.get("kind") == "search-result"
        ]
        if search_items:
            sections.append(self._build_search_results_section(search_items))

        # Web content section
        web_items = [
            s for s in snapshots if s.metadata.get("kind") == "web-content"
        ]
        if web_items:
            sections.append(self._build_web_content_section(web_items))

        # Key findings table
        sections.append(self._build_findings_section(snapshots))

        # Extra sections from caller
        if extra_sections:
            for extra in extra_sections:
                sections.append(
                    ReportSection(
                        title=extra.get("title", "Additional"),
                        body=extra.get("body", ""),
                        tables=extra.get("tables"),
                        metadata=extra.get("metadata"),
                    )
                )

        return ReportPayload(
            title=f"Research Report: {query[:100]}",
            subtitle=f"Evidence from {len(snapshots)} sources",
            author="Regent Discovery Engine",
            sections=sections,
            metadata={
                "query": query,
                "total_snapshots": len(snapshots),
                "generated_at": datetime.now(UTC).isoformat(),
                "source_types": list({s.metadata.get("connector", "?") for s in snapshots}),
            },
        )

    def generate_artifacts(
        self, payload: ReportPayload
    ) -> list[GeneratedArtifact]:
        """Generate both Markdown and HTML artifacts from a report payload."""
        md_gen = MarkdownReportGenerator()
        html_gen = HTMLReportGenerator()
        return [md_gen.generate(payload), html_gen.generate(payload)]

    def _build_summary_section(
        self, query: str, snapshots: list[EvidenceSourceSnapshot]
    ) -> ReportSection:
        connectors = {s.metadata.get("connector", "?") for s in snapshots}
        avg_quality = 0.0
        quality_count = 0
        for s in snapshots:
            q = s.metadata.get("quality")
            if isinstance(q, (int, float)):
                avg_quality += q
                quality_count += 1
        if quality_count:
            avg_quality /= quality_count

        body = (
            f"This report presents research findings for the query: \"{query}\".\n\n"
            f"A total of {len(snapshots)} evidence snapshots were collected from "
            f"{len(connectors)} source type(s): {', '.join(sorted(connectors))}.\n\n"
        )
        if quality_count:
            body += f"Average content quality score: {avg_quality:.2f}/1.00."

        return ReportSection(
            title="Executive Summary",
            body=body,
            metadata={"query": query, "snapshot_count": len(snapshots)},
        )

    def _build_evidence_section(
        self, connector_name: str, items: list[EvidenceSourceSnapshot]
    ) -> ReportSection | None:
        if not items:
            return None
        body = f"Evidence from connector: {connector_name} ({len(items)} items).\n\n"
        for i, item in enumerate(items[: self._max_per_section], 1):
            title = item.metadata.get("title", "")
            source = item.source_uri
            body += f"{i}. [{title or 'Untitled'}]({source})\n"
        return ReportSection(
            title=f"Evidence: {connector_name}",
            body=body,
        )

    def _build_search_results_section(
        self, items: list[EvidenceSourceSnapshot]
    ) -> ReportSection:
        columns = ["#", "Title", "URL", "Snippet"]
        rows: list[list] = []
        for i, item in enumerate(items[: self._max_per_section], 1):
            rows.append([
                i,
                item.metadata.get("title", "")[:80],
                item.source_uri[:60],
                item.metadata.get("snippet", "")[:120],
            ])
        return ReportSection(
            title="Search Results",
            body=f"Found {len(items)} search results.",
            tables=[{"columns": columns, "rows": rows}],
        )

    def _build_web_content_section(
        self, items: list[EvidenceSourceSnapshot]
    ) -> ReportSection:
        columns = ["Title", "Words", "Quality", "URL"]
        rows: list[list] = []
        for item in items[: self._max_per_section]:
            rows.append([
                item.metadata.get("title", "")[:60],
                item.metadata.get("word_count", 0),
                f"{item.metadata.get('quality', 0):.2f}",
                item.source_uri[:50],
            ])
        return ReportSection(
            title="Web Content Analysis",
            body=f"Extracted content from {len(items)} web pages.",
            tables=[{"columns": columns, "rows": rows}],
        )

    def _build_findings_section(
        self, snapshots: list[EvidenceSourceSnapshot]
    ) -> ReportSection:
        columns = ["Source", "Type", "Quality", "Size"]
        rows: list[list] = []
        for item in snapshots[: self._max_per_section]:
            rows.append([
                item.source_uri[:50],
                item.metadata.get("kind", "unknown"),
                str(item.metadata.get("quality", "N/A")),
                str(item.metadata.get("byte_size", 0)),
            ])
        return ReportSection(
            title="Key Findings Summary",
            body="Overview of all evidence collected.",
            tables=[{"columns": columns, "rows": rows}],
        )
