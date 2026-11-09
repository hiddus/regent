"""Live product-surface QA against a public Preview URL.

Process readiness (HTTP 200 on ``/``) is not product readiness. This probe
checks that stylesheets have real design substance and that primary in-app
navigation works **through the same URL users open**.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_STYLESHEET_HREF_RE = re.compile(
    r"""<link\b[^>]*rel\s*=\s*["']stylesheet["'][^>]*href\s*=\s*["']([^"']+)["']"""
    r"""|<link\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*rel\s*=\s*["']stylesheet["']""",
    re.IGNORECASE,
)
_INLINE_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_INTERNAL_HREF_RE = re.compile(
    r"""<a\b[^>]*href\s*=\s*["']([^"'#][^"']*)["']""",
    re.IGNORECASE,
)
_MAIN_RE = re.compile(r"<main\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

# Minimum CSS body length to count as intentional design (not a stub).
_MIN_CSS_CHARS = 800
# UX design signals that distinguish a designed surface from browser defaults.
_CSS_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("font-family", re.compile(r"font-family\s*:", re.I)),
    ("max-width", re.compile(r"max-width\s*:", re.I)),
    ("spacing", re.compile(r"(?:padding|margin|gap)\s*:", re.I)),
    ("color-token", re.compile(r"(?:--[a-z0-9_-]+\s*:|color\s*:)", re.I)),
    ("hover", re.compile(r":hover\b", re.I)),
    ("layout", re.compile(r"display\s*:\s*(?:flex|grid)\b", re.I)),
)
_MIN_CSS_SIGNALS = 4
# Empty / tiny inline style must not green-pass asset reachability.
_MIN_INLINE_STYLE_CHARS = 120
# Content products with enough list links must mostly navigate successfully.
_NAV_PASS_RATIO = 0.8
_MIN_DETAIL_VISIBLE_CHARS = 80
# Feed/list-like home pages without any content links fail product QA.
# Keep English tokens word-bounded — bare "read" matches "already"/"breadcrumb".
_LIST_PRODUCT_HINTS = re.compile(
    r"(?:\b(?:items?|articles?|posts?|details?|digest|feed|news)\b|"
    r"情报|资讯|论文|必读|今日必读)",
    re.IGNORECASE,
)
# Utility / single-purpose surfaces (clock, todo, generators) do not need list nav.
_UTILITY_SURFACE_HINTS = re.compile(
    r"(?:\b(?:time|clock|timer|todo|upload|generator|now|beijing)\b|"
    r"时间|时钟|待办|上传|生成器|北京)",
    re.IGNORECASE,
)
# Content-product APIs: thin seed catalogs must not soft-pass as demos.
_MIN_COUNTRY_POINTS = 10
_MIN_CROSSWALK_STEPS = 10
# Each group is independently required — a long scenario must not waive thin obligations.
_REQUIRED_POINT_FIELD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("title", "name"),
    ("statute", "法源"),
    ("source", "source_url"),
    ("obligations", "obligation", "body", "义务"),
    ("scenario", "detail"),
    ("risk", "风险"),
)
_REQUIRED_STEP_FIELD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("trigger", "触发"),
    ("action", "动作"),
    ("check",),
    ("evidence", "证据"),
    ("owner", "责任方", "owner_role"),
    ("priority", "优先级"),
)
# Present-but-thin fields are outlines, not operable handbook detail.
_MIN_POINT_FIELD_CHARS: dict[str, int] = {
    "title": 8,
    "name": 8,
    "statute": 6,
    "source": 12,
    "source_url": 12,
    "法源": 6,
    "obligations": 80,
    "obligation": 80,
    "body": 80,
    "scenario": 40,
    "detail": 40,
    "义务": 80,
    "risk": 50,
    "风险": 50,
}
_MIN_STEP_FIELD_CHARS: dict[str, int] = {
    "trigger": 24,
    "触发": 24,
    "action": 40,
    "check": 24,
    "动作": 40,
    "evidence": 24,
    "证据": 24,
    "owner": 2,
    "责任方": 2,
    "owner_role": 2,
    "priority": 1,
    "优先级": 1,
}
_CONTENT_API_HINTS = re.compile(
    r"(?:/api/countries|/api/crosswalks|跨境|合规|Crosswalk|PDPA|CCPA)",
    re.IGNORECASE,
)


def _has_any_field(row: dict[str, Any], names: tuple[str, ...]) -> bool:
    lower_map = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        val = lower_map.get(name.lower())
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            return True
        if not isinstance(val, str) and val not in (None, [], {}, ()):
            return True
    return False


def _field_text_for_group(row: dict[str, Any], names: tuple[str, ...]) -> str:
    lower_map = {str(k).lower(): v for k, v in row.items()}
    best = ""
    for name in names:
        val = lower_map.get(name.lower())
        if isinstance(val, str) and val.strip():
            if len(val.strip()) > len(best):
                best = val.strip()
        elif val not in (None, [], {}, ()) and not isinstance(val, str):
            text = str(val)
            if len(text) > len(best):
                best = text
    return best


def _missing_field_groups(
    row: dict[str, Any],
    groups: tuple[tuple[str, ...], ...],
    *,
    min_chars: dict[str, int] | None = None,
) -> list[str]:
    missing: list[str] = []
    mins = min_chars or {}
    for group in groups:
        text = _field_text_for_group(row, group)
        need = max((mins.get(n.lower(), mins.get(n, 1)) for n in group), default=1)
        if not text:
            missing.append("|".join(group[:3]))
        elif len(text) < need:
            missing.append(f"{'|'.join(group[:2])}:thin:{len(text)}<{need}")
    return missing



@dataclass(frozen=True, slots=True)
class LivePreviewCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LivePreviewQaResult:
    passed: bool
    checks: list[LivePreviewCheck] = field(default_factory=list)
    summary: str = ""

    def failed_gap_codes(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]

    def concrete_failure_reasons(self) -> list[str]:
        """Gap reasons that keep check detail (URLs/status) for learning loops."""
        out: list[str] = []
        for check in self.checks:
            if check.passed:
                continue
            detail = (check.detail or "").strip()
            out.append(f"{check.name}: {detail}" if detail else check.name)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "failed_gaps": self.failed_gap_codes(),
            "concrete_failures": self.concrete_failure_reasons(),
        }


def _same_origin(base: str, candidate: str) -> bool:
    b, c = urlparse(base), urlparse(candidate)
    if c.scheme in {"mailto", "javascript", "data"}:
        return False
    if not c.netloc:
        return True
    return (c.scheme, c.netloc) == (b.scheme, b.netloc)


def _visible_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _css_signal_hits(css_text: str) -> list[str]:
    return [name for name, pat in _CSS_SIGNAL_PATTERNS if pat.search(css_text or "")]


def _score_css_substance(css_text: str) -> tuple[bool, str]:
    body = (css_text or "").strip()
    if len(body) < _MIN_CSS_CHARS:
        return False, f"css {len(body)} chars < {_MIN_CSS_CHARS}"
    hits = _css_signal_hits(body)
    if len(hits) < _MIN_CSS_SIGNALS:
        return (
            False,
            f"style signals={len(hits)} < {_MIN_CSS_SIGNALS} ({','.join(hits) or 'none'})",
        )
    return True, f"css {len(body)} chars signals={','.join(hits)}"


def _inline_style_bodies(html: str) -> list[str]:
    return [(m.group(1) or "").strip() for m in _INLINE_STYLE_RE.finditer(html or "")]


def _join_preview(base: str, path: str) -> str:
    """Join a path under the Preview mount.

    ``urljoin(base, "/api/...")`` drops ``/preview/runtime/<id>/`` because a
    leading slash is treated as origin-absolute. Preview APIs must stay under
    the same public prefix users open.
    """
    root = base if base.endswith("/") else f"{base}/"
    return urljoin(root, (path or "").lstrip("/"))


def _pick_nav_candidates(html: str, *, base_url: str, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in _INTERNAL_HREF_RE.finditer(html):
        raw = (match.group(1) or "").strip()
        if not raw or raw.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        abs_url = urljoin(base_url, raw)
        if not _same_origin(base_url, abs_url):
            continue
        path = urlparse(abs_url).path.lower()
        if path.endswith(
            (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2")
        ):
            continue
        # Skip pure API JSON endpoints as "detail page" candidates.
        if "/api/" in path:
            continue
        home = urlparse(base_url).path.rstrip("/") or "/"
        cand_path = urlparse(abs_url).path.rstrip("/") or "/"
        if cand_path == home:
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append(abs_url)
        if len(out) >= limit:
            break
    return out


def _looks_like_list_product(html: str) -> bool:
    text = html or ""
    lower = text.lower()
    # Utility pages: do not require list→detail navigation.
    if _UTILITY_SURFACE_HINTS.search(text) and lower.count("<article") < 2:
        return False
    if _LIST_PRODUCT_HINTS.search(text):
        return True
    # Multiple article/card blocks imply a list surface.
    return lower.count("<article") >= 2 or lower.count('class="card') >= 2


async def run_live_preview_qa(
    browse_url: str,
    *,
    timeout_seconds: float = 12.0,
    client: httpx.AsyncClient | None = None,
    require_internal_nav: bool | None = None,
) -> LivePreviewQaResult:
    """Probe public Preview URL for shippable product surface signals."""
    base = (browse_url or "").strip()
    checks: list[LivePreviewCheck] = []
    if not base.startswith(("http://", "https://")):
        return LivePreviewQaResult(
            passed=False,
            checks=[
                LivePreviewCheck(
                    "preview-browse-url",
                    False,
                    "missing public browse URL for product QA",
                )
            ],
            summary="rejected: no public preview URL",
        )

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True)
    try:
        try:
            home = await http.get(base)
        except Exception as exc:  # noqa: BLE001
            checks.append(
                LivePreviewCheck("preview-home-reachable", False, str(exc)[:200])
            )
            return LivePreviewQaResult(
                passed=False,
                checks=checks,
                summary="rejected: preview home unreachable",
            )

        home_ok = home.status_code < 400 and "html" in (
            home.headers.get("content-type") or ""
        ).lower()
        checks.append(
            LivePreviewCheck(
                "preview-home-reachable",
                home_ok,
                f"status={home.status_code} type={home.headers.get('content-type')}",
            )
        )
        html = home.text if home_ok else ""
        if not home_ok:
            return LivePreviewQaResult(
                passed=False,
                checks=checks,
                summary="rejected: preview home not HTML",
            )

        has_main = bool(_MAIN_RE.search(html))
        checks.append(
            LivePreviewCheck(
                "semantic-main",
                has_main,
                "missing <main>" if not has_main else "ok",
            )
        )

        inline_bodies = _inline_style_bodies(html)
        inline_joined = "\n".join(inline_bodies)
        substantial_inline = len(inline_joined) >= _MIN_INLINE_STYLE_CHARS
        sheet_hrefs: list[str] = []
        for match in _STYLESHEET_HREF_RE.finditer(html):
            href = (match.group(1) or match.group(2) or "").strip()
            if href:
                sheet_hrefs.append(urljoin(base, href))

        if not inline_bodies and not sheet_hrefs:
            checks.append(
                LivePreviewCheck(
                    "stylesheet-present",
                    False,
                    "no <style> or stylesheet link (browser-default dump)",
                )
            )
        elif inline_bodies and not substantial_inline and not sheet_hrefs:
            checks.append(
                LivePreviewCheck(
                    "stylesheet-present",
                    False,
                    f"inline <style> only {len(inline_joined)} chars "
                    f"(need >={_MIN_INLINE_STYLE_CHARS} or external CSS)",
                )
            )
        else:
            checks.append(
                LivePreviewCheck(
                    "stylesheet-present",
                    True,
                    (
                        f"inline {len(inline_joined)} chars"
                        if substantial_inline and not sheet_hrefs
                        else f"links={len(sheet_hrefs)}"
                    ),
                )
            )

        # Collect CSS text for substance scoring (external preferred).
        css_blobs: list[str] = []
        asset_ok = False
        asset_details: list[str] = []
        if sheet_hrefs:
            for href in sheet_hrefs[:4]:
                try:
                    resp = await http.get(href)
                    ctype = (resp.headers.get("content-type") or "").lower()
                    body = resp.text if resp.status_code < 400 else ""
                    looks_css = (
                        resp.status_code < 400
                        and (
                            "css" in ctype
                            or "{" in body
                            or ":" in body
                        )
                        and "<html" not in body.lower()[:200]
                    )
                    asset_details.append(
                        f"{href} → {resp.status_code}/{ctype or 'no-type'}/{len(body)}b"
                    )
                    if looks_css:
                        asset_ok = True
                        css_blobs.append(body)
                        break
                except Exception as exc:  # noqa: BLE001
                    asset_details.append(f"{href} → {type(exc).__name__}")
        elif substantial_inline:
            asset_ok = True
            css_blobs.append(inline_joined)
            asset_details.append(f"inline style {len(inline_joined)} chars")
        else:
            asset_details.append("no reachable stylesheet body")

        checks.append(
            LivePreviewCheck(
                "preview-asset-reachability",
                asset_ok,
                "; ".join(asset_details)[:400]
                if asset_ok
                else f"stylesheet not reachable via preview URL: {'; '.join(asset_details)[:360]}",
            )
        )

        css_text = "\n".join(css_blobs)
        substance_ok, substance_detail = _score_css_substance(css_text)
        # If assets failed, substance fails with the same root cause.
        if not asset_ok:
            substance_ok = False
            substance_detail = "no CSS body to score"
        checks.append(
            LivePreviewCheck(
                "stylesheet-substance",
                substance_ok,
                substance_detail,
            )
        )
        signal_hits = _css_signal_hits(css_text)
        styled_ok = substance_ok and len(signal_hits) >= _MIN_CSS_SIGNALS
        checks.append(
            LivePreviewCheck(
                "styled-surface",
                styled_ok,
                (
                    f"style signals={len(signal_hits)} ({','.join(signal_hits)})"
                    if styled_ok
                    else f"style signals={len(signal_hits)} < {_MIN_CSS_SIGNALS} "
                    f"(not a designed product UI)"
                ),
            )
        )

        nav_urls = _pick_nav_candidates(html, base_url=base)
        must_nav = (
            require_internal_nav
            if require_internal_nav is not None
            else _looks_like_list_product(html)
        )
        if not nav_urls:
            if must_nav:
                checks.append(
                    LivePreviewCheck(
                        "preview-internal-nav",
                        False,
                        "list/feed product has no internal content links to probe",
                    )
                )
            else:
                checks.append(
                    LivePreviewCheck(
                        "preview-internal-nav",
                        True,
                        "no internal content links required for this surface",
                    )
                )
        else:
            nav_ok_count = 0
            nav_details: list[str] = []
            for url in nav_urls:
                try:
                    resp = await http.get(url)
                    ctype = (resp.headers.get("content-type") or "").lower()
                    body = resp.text if resp.status_code < 400 else ""
                    visible = _visible_text(body) if "html" in ctype else ""
                    ok = (
                        resp.status_code < 400
                        and "html" in ctype
                        and len(visible) >= _MIN_DETAIL_VISIBLE_CHARS
                    )
                    nav_details.append(
                        f"{url} → {resp.status_code}/{ctype or 'no-type'}/vis={len(visible)}"
                    )
                    if ok:
                        nav_ok_count += 1
                except Exception as exc:  # noqa: BLE001
                    nav_details.append(f"{url} → {type(exc).__name__}")
            ratio = nav_ok_count / max(len(nav_urls), 1)
            # Single link: must pass. Two+: require pass ratio.
            if len(nav_urls) == 1:
                nav_ok = nav_ok_count == 1
            else:
                nav_ok = ratio >= _NAV_PASS_RATIO
            checks.append(
                LivePreviewCheck(
                    "preview-internal-nav",
                    nav_ok,
                    (
                        f"nav ok {nav_ok_count}/{len(nav_urls)} "
                        f"({ratio:.0%}); " + "; ".join(nav_details)
                    )[:400]
                    if nav_ok
                    else (
                        f"detail/nav pass-rate {nav_ok_count}/{len(nav_urls)} "
                        f"<{_NAV_PASS_RATIO:.0%}: " + "; ".join(nav_details)
                    )[:400],
                )
            )

        depth = await _probe_content_catalog_depth(http, base=base, home_html=html)
        if depth is not None:
            checks.append(depth)

        passed = all(c.passed for c in checks)
        return LivePreviewQaResult(
            passed=passed,
            checks=checks,
            summary=(
                "product surface ready"
                if passed
                else "rejected: live preview is not a usable product surface"
            ),
        )
    finally:
        if owns_client:
            await http.aclose()


async def _probe_content_catalog_depth(
    http: httpx.AsyncClient,
    *,
    base: str,
    home_html: str,
) -> LivePreviewCheck | None:
    """Fail closed when a compliance/catalog product ships thin seed APIs."""
    if not _CONTENT_API_HINTS.search(home_html or ""):
        # Only enforce for surfaces that advertise this product shape.
        return None
    details: list[str] = []
    try:
        countries_resp = await http.get(_join_preview(base, "/api/countries"))
    except Exception as exc:  # noqa: BLE001
        return LivePreviewCheck(
            "preview-content-depth",
            False,
            f"/api/countries unreachable: {exc}"[:200],
        )
    if countries_resp.status_code >= 400:
        return LivePreviewCheck(
            "preview-content-depth",
            False,
            f"/api/countries → {countries_resp.status_code}",
        )
    try:
        countries = countries_resp.json()
    except Exception:  # noqa: BLE001
        return LivePreviewCheck(
            "preview-content-depth",
            False,
            "/api/countries not JSON",
        )
    if not isinstance(countries, list) or not countries:
        return LivePreviewCheck(
            "preview-content-depth",
            False,
            "/api/countries empty",
        )
    thin_countries: list[str] = []
    weak_fields: list[str] = []
    for item in countries:
        if not isinstance(item, dict):
            continue
        code = str(item.get("country_code") or item.get("code") or "?")
        points = item.get("points") or []
        n = len(points) if isinstance(points, list) else 0
        details.append(f"{code}.points={n}")
        if n < _MIN_COUNTRY_POINTS:
            thin_countries.append(f"{code}:{n}")
        if isinstance(points, list):
            for idx, point in enumerate(points[:12]):
                if not isinstance(point, dict):
                    weak_fields.append(f"{code}[{idx}]:not-object")
                    continue
                miss = _missing_field_groups(
                    point,
                    _REQUIRED_POINT_FIELD_GROUPS,
                    min_chars=_MIN_POINT_FIELD_CHARS,
                )
                if miss:
                    weak_fields.append(f"{code}[{idx}]:missing:{','.join(miss)}")
                    if len(weak_fields) >= 8:
                        break
    # Probe pairwise crosswalk APIs referenced by common demo routes.
    thin_crosswalks: list[str] = []
    for pair in ("US-SG", "SG-US"):
        path = f"/api/crosswalks/{pair}"
        try:
            resp = await http.get(_join_preview(base, path))
        except Exception as exc:  # noqa: BLE001
            thin_crosswalks.append(f"{pair}:err:{type(exc).__name__}")
            continue
        if resp.status_code >= 400:
            thin_crosswalks.append(f"{pair}:http:{resp.status_code}")
            continue
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            thin_crosswalks.append(f"{pair}:not-json")
            continue
        steps = payload.get("steps") if isinstance(payload, dict) else None
        n = len(steps or []) if isinstance(steps, list) else 0
        details.append(f"{pair}.steps={n}")
        if n < _MIN_CROSSWALK_STEPS:
            thin_crosswalks.append(f"{pair}:{n}")
        if isinstance(steps, list):
            for idx, step in enumerate(steps[:12]):
                if not isinstance(step, dict):
                    weak_fields.append(f"{pair}[{idx}]:not-object")
                    continue
                miss = _missing_field_groups(
                    step,
                    _REQUIRED_STEP_FIELD_GROUPS,
                    min_chars=_MIN_STEP_FIELD_CHARS,
                )
                if miss:
                    weak_fields.append(f"{pair}[{idx}]:missing:{','.join(miss)}")
                    if len(weak_fields) >= 12:
                        break

    ok = not thin_countries and not thin_crosswalks and not weak_fields
    detail = "; ".join(details)[:360]
    if not ok:
        detail = (
            f"thin catalog countries={thin_countries or '-'} "
            f"crosswalks={thin_crosswalks or '-'} "
            f"weak_fields={weak_fields[:6] or '-'}; {detail}"
        )[:400]
    return LivePreviewCheck("preview-content-depth", ok, detail)
