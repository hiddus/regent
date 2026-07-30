"""Delivery-oriented review (capability delivery-review-v1).

Fail-closed against demos AND against Goal-not-attained surfaces.
Pass means: evidence that the Goal's deliverable/success criteria are met enough to ship.
Heuristics (stylesheet, structure) are necessary but not sufficient — Goal attainment is the gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from regent.application.goal_anchor_service import validate_goal_alignment
from regent.infrastructure.delivery_review_capability import (
    CAPABILITY_NAME,
    load_delivery_review_capability_package,
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_STYLESHEET_LINK_RE = re.compile(
    r"<link\b[^>]*rel\s*=\s*[\"']stylesheet[\"'][^>]*>",
    re.I,
)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_HTTP_HREF_RE = re.compile(r"""href\s*=\s*["'](https?://[^"']+)["']""", re.I)


@dataclass(frozen=True, slots=True)
class DeliveryReviewCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryReviewResult:
    passed: bool
    capability: str
    checks: list[DeliveryReviewCheck] = field(default_factory=list)
    summary: str = ""

    def failed_gap_codes(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]

    def raise_if_failed(self) -> None:
        if self.passed:
            return
        failed = [c for c in self.checks if not c.passed]
        reasons = "; ".join(f"{c.name}: {c.detail or 'failed'}" for c in failed)
        raise ValueError(f"{self.capability} rejected non-deliverable surface: {reasons}")


def _strip_tags(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


def _first_match_text(pattern: re.Pattern[str], html: str) -> str:
    match = pattern.search(html)
    if match is None:
        return ""
    return _strip_tags(match.group(1)).strip()


def _rules(goal_scale: str | None = None) -> dict[str, Any]:
    package = load_delivery_review_capability_package()
    verification = dict(package.verification or {})
    rules = dict(verification.get("rules") or {})
    # SMALL goals get relaxed thresholds — they are single-milestone, simpler products.
    if goal_scale == "SMALL":
        rules.setdefault("min_structure_signals", 1)
        rules["min_structure_signals"] = min(int(rules.get("min_structure_signals") or 2), 1)
        rules.setdefault("min_style_signals", 2)
        rules["min_style_signals"] = min(int(rules.get("min_style_signals") or 4), 2)
        rules.setdefault("min_visible_text_chars", 120)
        rules["min_visible_text_chars"] = min(int(rules.get("min_visible_text_chars") or 280), 120)
    return rules


def _extract_style_text(html: str) -> str:
    """Collect inline <style> CSS text (comments stripped). External CSS is signaled separately."""
    chunks: list[str] = []
    for match in _STYLE_BLOCK_RE.finditer(html):
        chunks.append(_CSS_COMMENT_RE.sub(" ", match.group(1)))
    return _WS_RE.sub(" ", " ".join(chunks)).strip()


def _has_stylesheet(html: str) -> bool:
    return bool(_STYLESHEET_LINK_RE.search(html)) or bool(_STYLE_BLOCK_RE.search(html))


def review_html_for_delivery(
    html: str,
    *,
    acceptance_contract: dict[str, Any] | None = None,
    success_criteria: dict[str, Any] | None = None,
) -> DeliveryReviewResult:
    """Review generated HTML as a deliverable product page, not a demo stub."""
    contract = dict(acceptance_contract or {})
    goal_scale = str(contract.get("goal_scale") or "")
    rules = _rules(goal_scale=goal_scale or None)
    criteria = dict(success_criteria or {})
    checks: list[DeliveryReviewCheck] = []
    lower = html.lower()
    visible = _strip_tags(html)
    title = _first_match_text(_TITLE_RE, html)
    h1 = _first_match_text(_H1_RE, html)
    heading = (h1 or title).strip().lower()
    style_text = _extract_style_text(html)
    style_lower = style_text.lower()
    has_external_css = bool(_STYLESHEET_LINK_RE.search(html))

    # Static/preview surfaces must not ship unrendered Jinja/Mustache markers.
    has_template_markers = "{{" in html or "{%" in html or "{#" in html
    checks.append(
        DeliveryReviewCheck(
            "forbid-unrendered-templates",
            not has_template_markers,
            "unrendered template markers ({{, {%, or {#) in HTML"
            if has_template_markers
            else "ok",
        )
    )

    if rules.get("require_semantic_main", True):
        ok = "<main" in lower
        checks.append(
            DeliveryReviewCheck(
                "semantic-main",
                ok,
                "missing <main>" if not ok else "ok",
            )
        )

    if rules.get("require_observation_hook", True):
        ok = "data-regent-event" in html
        checks.append(
            DeliveryReviewCheck(
                "observation-hook",
                ok,
                "missing data-regent-event" if not ok else "ok",
            )
        )

    # Publishable surface must not be browser-default unstyled dump.
    if rules.get("require_stylesheet", True):
        ok_sheet = _has_stylesheet(html)
        checks.append(
            DeliveryReviewCheck(
                "stylesheet-present",
                ok_sheet,
                "missing <style> or stylesheet link (browser-default dump)"
                if not ok_sheet
                else "ok",
            )
        )
        min_style = int(rules.get("min_style_chars") or 220)
        # External stylesheet counts as intentional design; inline CSS must be substantial.
        ok_style_len = has_external_css or len(style_text) >= min_style
        checks.append(
            DeliveryReviewCheck(
                "stylesheet-substance",
                ok_style_len,
                f"inline CSS {len(style_text)} chars < {min_style} (unstyled/demo likely)"
                if not ok_style_len
                else (
                    "external stylesheet"
                    if has_external_css
                    else f"inline CSS {len(style_text)} chars"
                ),
            )
        )
        signals = [str(item).lower() for item in (rules.get("style_signals") or [])]
        style_hits = sum(1 for sig in signals if sig in style_lower)
        # External CSS file: cannot inspect bytes here; require at least the link.
        min_signals = int(rules.get("min_style_signals") or 4)
        ok_signals = has_external_css or style_hits >= min_signals
        checks.append(
            DeliveryReviewCheck(
                "styled-surface",
                ok_signals,
                f"style signals={style_hits} < {min_signals} (not a designed product UI)"
                if not ok_signals
                else (
                    "external stylesheet"
                    if has_external_css
                    else f"style signals={style_hits}"
                ),
            )
        )

    min_chars = int(rules.get("min_visible_text_chars") or 280)
    ok_len = len(visible) >= min_chars
    checks.append(
        DeliveryReviewCheck(
            "min-visible-text",
            ok_len,
            f"{len(visible)} chars < {min_chars} (demo/stub likely)"
            if not ok_len
            else f"{len(visible)} chars",
        )
    )

    stub_titles = [str(item).lower() for item in (rules.get("stub_titles") or [])]
    is_stub_title = False
    if heading:
        for stub in stub_titles:
            if heading == stub or heading.startswith(stub + " ") or heading.startswith(stub + ":"):
                is_stub_title = True
                break
    # Short page whose only heading is a stub title → reject.
    stub_shell = is_stub_title and len(visible) < max(min_chars * 2, 500)
    checks.append(
        DeliveryReviewCheck(
            "forbid-demo-shell",
            not stub_shell,
            f"stub title/shell: {heading!r}" if stub_shell else "ok",
        )
    )

    selectors = [str(item).lower() for item in (rules.get("structure_selectors") or [])]
    structure_hits = sum(1 for sel in selectors if sel in lower)
    min_structure = int(rules.get("min_structure_signals") or 2)
    ok_structure = structure_hits >= min_structure
    checks.append(
        DeliveryReviewCheck(
            "product-structure",
            ok_structure,
            f"structure signals={structure_hits} < {min_structure}"
            if not ok_structure
            else f"structure signals={structure_hits}",
        )
    )

    # Observed evidence must appear when contract demands it.
    if contract.get("must_render_observed_entries"):
        entries = [
            item
            for item in (contract.get("observed_evidence_entries") or [])
            if isinstance(item, dict)
        ]
        titles = [
            str(item.get("title") or "").strip()
            for item in entries
            if str(item.get("title") or "").strip()
        ]
        required = min(
            int(rules.get("min_observed_titles_rendered") or 3),
            len(titles),
        )
        rendered = sum(1 for t in titles if t and t.lower() in visible.lower())
        ok_obs = required == 0 or rendered >= required
        checks.append(
            DeliveryReviewCheck(
                "observed-entries-rendered",
                ok_obs,
                f"rendered {rendered}/{required} observed titles"
                if not ok_obs
                else f"rendered {rendered} observed titles",
            )
        )

    # success_criteria: Goal attainment gate (ATTRIBUTE_6 / ATTRIBUTE_1).
    required_phrases = criteria.get("required_phrases") or contract.get("required_phrases") or []
    if isinstance(required_phrases, list) and required_phrases:
        missing = [
            str(p)
            for p in required_phrases
            if str(p).strip() and str(p).lower() not in visible.lower()
        ]
        checks.append(
            DeliveryReviewCheck(
                "required-phrases",
                not missing,
                f"missing {missing[:5]}" if missing else "ok",
            )
        )

    min_list_items = criteria.get("min_list_items") or contract.get("min_list_items")
    if min_list_items is not None:
        try:
            need = int(min_list_items)
        except (TypeError, ValueError):
            need = 0
        if need > 0:
            li_count = len(re.findall(r"<li\b", lower))
            article_count = len(re.findall(r"<article\b", lower))
            ok_li = max(li_count, article_count) >= need
            checks.append(
                DeliveryReviewCheck(
                    "min-list-items",
                    ok_li,
                    f"items={max(li_count, article_count)} < {need}"
                    if not ok_li
                    else f"items={max(li_count, article_count)}",
                )
            )

    # Outbound links: news/digest Goals need real destinations, not dead stubs.
    min_links = criteria.get("min_outbound_links") or contract.get("min_outbound_links")
    if min_links is None and (
        contract.get("must_render_observed_entries")
        or "digest" in heading
        or "news" in heading
        or "头条" in visible
        or "新闻" in visible
    ):
        min_links = 3
    if min_links is not None:
        try:
            need_links = int(min_links)
        except (TypeError, ValueError):
            need_links = 0
        if need_links > 0:
            link_count = len(_HTTP_HREF_RE.findall(html))
            ok_links = link_count >= need_links
            checks.append(
                DeliveryReviewCheck(
                    "goal-outbound-links",
                    ok_links,
                    f"https links={link_count} < {need_links} (Goal deliverable unmet)"
                    if not ok_links
                    else f"https links={link_count}",
                )
            )

    # first_deliverable / goal intent: page must not ignore stated deliverable keywords.
    first_deliverable = str(
        contract.get("first_deliverable") or criteria.get("first_deliverable") or ""
    ).strip()
    if first_deliverable and len(first_deliverable) >= 12:
        tokens = [
            t.lower()
            for t in re.findall(r"[\w\u4e00-\u9fff]{2,}", first_deliverable)
            if t.lower()
            not in {
                "with",
                "from",
                "that",
                "this",
                "have",
                "page",
                "html",
                "static",
                "minimal",
                "basic",
                "and",
                "the",
                "for",
                "a",
                "an",
                "to",
                "of",
                "in",
                "on",
            }
        ][:8]
        if tokens:
            hits = sum(1 for t in tokens if t in visible.lower() or t in heading)
            need_hits = max(1, min(2, len(tokens) // 3 or 1))
            ok_intent = hits >= need_hits
            checks.append(
                DeliveryReviewCheck(
                    "goal-first-deliverable",
                    ok_intent,
                    f"deliverable keyword hits={hits} < {need_hits}"
                    if not ok_intent
                    else f"deliverable keyword hits={hits}",
                )
            )

    # Explicit anti-demo tokens dominating the page.
    demo_tokens = ("lorem ipsum", "this is a demo", "demo only", "sample placeholder")
    demo_hit = any(token in visible.lower() for token in demo_tokens)
    checks.append(
        DeliveryReviewCheck(
            "forbid-demo-copy",
            not demo_hit,
            "demo/placeholder copy detected" if demo_hit else "ok",
        )
    )

    # GAC-GA: GoalAnchor alignment check — validate HTML against original goal text.
    goal_anchor_text = str(contract.get("goal_anchor_text") or "").strip()
    if goal_anchor_text and len(goal_anchor_text) >= 5:
        alignment = validate_goal_alignment(
            html,
            goal_anchor_text,
            success_criteria=criteria,
            first_deliverable=first_deliverable,
        )
        checks.append(
            DeliveryReviewCheck(
                "goal-anchor-alignment",
                alignment.aligned,
                f"score={alignment.score:.0%} — {'; '.join(alignment.details[:3])}",
            )
        )

    passed = all(c.passed for c in checks)
    summary = (
        "goal attained (deliverable)"
        if passed
        else "rejected: Goal not attained — surface is not shippable"
    )
    return DeliveryReviewResult(
        passed=passed,
        capability=CAPABILITY_NAME,
        checks=checks,
        summary=summary,
    )


def review_files_for_delivery(
    files: dict[str, str],
    *,
    acceptance_contract: dict[str, Any] | None = None,
    success_criteria: dict[str, Any] | None = None,
    project_checks: bool = True,
) -> DeliveryReviewResult:
    html = files.get("index.html") or files.get("index.HTML") or ""
    if not html:
        # Prefer any *.html
        for name, content in files.items():
            if name.lower().endswith(".html") and content:
                html = content
                break
    if not html:
        return DeliveryReviewResult(
            passed=False,
            capability=CAPABILITY_NAME,
            checks=[
                DeliveryReviewCheck("index-html", False, "no HTML file to review"),
            ],
            summary="rejected: no HTML deliverable",
        )
    # Start with HTML review
    result = review_html_for_delivery(
        html,
        acceptance_contract=acceptance_contract,
        success_criteria=success_criteria,
    )
    # Skip project-level checks for frozen static app set
    frozen_static = set(files) == {"index.html", "styles.css", "app.js"}
    if not project_checks or frozen_static:
        return result
    # Extend with project-level checks
    all_checks = list(result.checks)
    contract = dict(acceptance_contract or {})
    allow_demo = str(contract.get("delivery_policy") or "").lower() == "demo"

    # Project structure: forbid trivial server patterns in Python files
    trivial_patterns = (
        "SimpleHTTPRequestHandler",
        "socketserver.TCPServer",
        "http.server",
    )
    py_content = ""
    py_files: list[str] = []
    for name, content in files.items():
        if name.endswith(".py") and content:
            py_content += content + "\n"
            py_files.append(name)
    is_trivial = any(pat in py_content for pat in trivial_patterns)
    all_checks.append(
        DeliveryReviewCheck(
            "forbid-trivial-server",
            not is_trivial,
            "trivial http.server template detected" if is_trivial else "ok",
        )
    )

    # Forbid pure static hosting disguised as an app (send_from_directory / StaticFiles only).
    static_only = _is_pure_static_backend(py_content, py_files)
    all_checks.append(
        DeliveryReviewCheck(
            "forbid-pure-static-backend",
            not static_only,
            (
                "backend is pure static file serving without business logic"
                if static_only
                else "ok"
            ),
        )
    )

    # Placeholder / fake demo content is FAIL unless Goal explicitly asks for demo.
    if not allow_demo:
        placeholder_hit = _has_forbidden_placeholder(files)
        all_checks.append(
            DeliveryReviewCheck(
                "forbid-placeholder-content",
                not placeholder_hit,
                (
                    f"placeholder/fake content detected: {placeholder_hit}"
                    if placeholder_hit
                    else "ok"
                ),
            )
        )

    # Project structure: require dependencies declaration
    has_requirements = any(
        name.lower() == "requirements.txt" for name in files
    )
    all_checks.append(
        DeliveryReviewCheck(
            "require-dependencies-declared",
            has_requirements,
            "missing requirements.txt" if not has_requirements else "ok",
        )
    )

    # Project structure: minimum file count
    min_files = 3
    file_count = len(files)
    ok_count = file_count >= min_files
    all_checks.append(
        DeliveryReviewCheck(
            "min-file-count",
            ok_count,
            f"project has {file_count} files < {min_files}"
            if not ok_count
            else f"{file_count} files",
        )
    )

    passed = all(c.passed for c in all_checks)
    summary = (
        "goal attained (deliverable)"
        if passed
        else "rejected: Goal not attained — surface is not shippable"
    )
    return DeliveryReviewResult(
        passed=passed,
        capability=result.capability,
        checks=all_checks,
        summary=summary,
    )


_STATIC_SERVE_RE = re.compile(
    r"(send_from_directory|StaticFiles|send_file\s*\()",
    re.I,
)
_DOMAIN_HINT_RE = re.compile(
    r"(sqlalchemy|sqlite3|create_engine|Session\(|\.query\(|"
    r"class\s+\w+\(.*Model|db\.Model|INSERT\s+INTO|SELECT\s+.+\s+FROM|"
    r"jsonify\(|return\s+\{)",
    re.I,
)
_PLACEHOLDER_RE = re.compile(
    r"(lorem ipsum|fake user|john doe|jane doe|sample user|"
    r"demo card|placeholder user|mock profile|hard-?coded demo|"
    r"example@example\.com|user\d+@example)",
    re.I,
)


def _is_pure_static_backend(py_content: str, py_files: list[str]) -> bool:
    """True when Python app only hosts static files with no domain logic."""
    if not py_content.strip():
        # No backend at all — treat as non-product unless HTML-only frozen set
        # (already skipped earlier).
        return True
    has_static = bool(_STATIC_SERVE_RE.search(py_content))
    has_domain = bool(_DOMAIN_HINT_RE.search(py_content))
    lines = [ln for ln in py_content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    tiny = len(lines) <= 20
    if has_static and tiny and not has_domain:
        return True
    if has_static and not has_domain and not any(
        hint in py_content for hint in ("jsonify", "request.", "Form(", "BaseModel")
    ):
        return True
    return False


def _has_forbidden_placeholder(files: dict[str, str]) -> str:
    for name, content in files.items():
        if not content:
            continue
        match = _PLACEHOLDER_RE.search(content)
        if match:
            return f"{name}:{match.group(0)}"
    return ""
