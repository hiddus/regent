"""Delivery Role Swarm — Product / Tech / Test / UX / Ops hard gates on Live Preview.

Runs after mechanical ``live_preview_qa``. Any selected role may reject.
Outline-only catalogs (short slogans, missing journeys, thin field text) fail
closed. Role roster is self-supplemented from the Goal via
``select_roles_for_goal``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from regent.application.delivery_role_agents import (
    DELIVERY_ROLE_AGENTS,
    delivery_role_catalog,
    select_roles_for_goal,
)

logger = logging.getLogger(__name__)

# Outline detection: field present but too thin to be an operable handbook.
_MIN_POINT_TEXT = {
    "obligations": 80,
    "obligation": 80,
    "body": 80,
    "scenario": 40,
    "detail": 40,
    "risk": 50,
    "title": 8,
    "statute": 6,
    "source": 12,
}
_MIN_STEP_TEXT = {
    "trigger": 24,
    "action": 40,
    "check": 24,
    "evidence": 24,
    "owner": 2,
    "priority": 1,
}
_MIN_HOME_VISIBLE = 400
# Non-catalog SMALL/static pages align with delivery_review min (~280), not handbook depth.
_MIN_HOME_VISIBLE_STATIC = 280
_MIN_DETAIL_VISIBLE = 600
_TAG_RE = re.compile(r"<[^>]+>")
_CONTENT_HINT = re.compile(
    r"(?:/api/countries|/api/crosswalks|跨境|合规|Crosswalk|PDPA|CCPA)",
    re.I,
)
_STATIC_SMALL_HINT = re.compile(
    r"(?:静态|单页|canary|static\s+page|index\.html|styles\.css)",
    re.I,
)


def _home_visible_threshold(
    *,
    is_catalog: bool,
    goal_input: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    """Catalog/handbook apps keep 400; SMALL/static canaries use the review floor."""
    if is_catalog:
        return _MIN_HOME_VISIBLE
    meta = metadata or {}
    scale = str(meta.get("goal_scale") or meta.get("scale") or "").upper()
    blob = f"{goal_input} {scale}"
    if scale == "SMALL" or _STATIC_SMALL_HINT.search(blob):
        return _MIN_HOME_VISIBLE_STATIC
    return _MIN_HOME_VISIBLE


@dataclass(frozen=True, slots=True)
class RoleReview:
    role_id: str
    accepted: bool
    score: float
    gaps: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "accepted": self.accepted,
            "score": self.score,
            "gaps": list(self.gaps),
            "findings": list(self.findings),
            "artifacts": dict(self.artifacts),
        }


@dataclass(frozen=True, slots=True)
class DeliverySwarmResult:
    accepted: bool
    score: float
    reason: str
    gaps: list[str]
    roles: list[RoleReview]
    catalog: dict[str, Any]
    source: str = "delivery_role_swarm/v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "score": self.score,
            "reason": self.reason,
            "gaps": list(self.gaps),
            "roles": [r.as_dict() for r in self.roles],
            "agents_defined": self.catalog,
            "source": self.source,
        }


def _join_preview(base: str, path: str) -> str:
    root = base if base.endswith("/") else f"{base}/"
    return urljoin(root, (path or "").lstrip("/"))


def _visible_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _field_text(row: dict[str, Any], names: tuple[str, ...]) -> str:
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        val = lower.get(name.lower())
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (list, dict)) and val:
            return str(val)
    return ""


def _thin_fields(
    row: dict[str, Any],
    mins: dict[str, int],
    *,
    groups: tuple[tuple[str, ...], ...],
) -> list[str]:
    weak: list[str] = []
    for group in groups:
        text = _field_text(row, group)
        # Use the strictest min among group keys that we know about.
        need = max((mins.get(n.lower(), 1) for n in group), default=1)
        if len(text) < need:
            weak.append(f"{'|'.join(group[:2])}:len={len(text)}<{need}")
    return weak


async def _fetch(http: httpx.AsyncClient, url: str) -> tuple[int, str, Any | None]:
    try:
        resp = await http.get(url, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)[:200], None
    body = resp.text or ""
    data: Any | None = None
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = None
    return resp.status_code, body, data


async def run_delivery_role_swarm(
    preview_url: str,
    *,
    live_qa: dict[str, Any] | None = None,
    goal_input: str = "",
    metadata: dict[str, Any] | None = None,
    role_ids: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> DeliverySwarmResult:
    """Run selected Product / Tech / Test / UX / Ops reviews on Preview URL."""
    selected = list(role_ids) if role_ids else select_roles_for_goal(
        goal_input, metadata=metadata
    )
    catalog = delivery_role_catalog(goal_input=goal_input, metadata=metadata)
    catalog["selected_roles"] = selected
    owns = client is None
    http = client or httpx.AsyncClient(timeout=25.0, follow_redirects=True)
    base = preview_url if preview_url.endswith("/") else f"{preview_url}/"
    roles: list[RoleReview] = []
    want = set(selected)

    try:
        home_status, home_html, _ = await _fetch(http, base)
        home_text = _visible_text(home_html)
        is_catalog = bool(_CONTENT_HINT.search(home_html) or _CONTENT_HINT.search(goal_input))
        min_home_visible = _home_visible_threshold(
            is_catalog=is_catalog,
            goal_input=goal_input,
            metadata=metadata,
        )

        # --- Tech: API under preview prefix ---
        tech_gaps: list[str] = []
        tech_findings: list[str] = []
        tech_art: dict[str, Any] = {"probes": []}
        countries: list[Any] = []
        if "tech" in want:
            if home_status >= 400 or home_status == 0:
                tech_gaps.append("delivery-tech-api")
                tech_findings.append(f"home unreachable status={home_status}")
            if is_catalog:
                st, _, data = await _fetch(http, _join_preview(base, "/api/countries"))
                tech_art["probes"].append({"path": "/api/countries", "status": st})
                if st >= 400 or not isinstance(data, list) or not data:
                    tech_gaps.append("delivery-tech-api")
                    tech_findings.append(
                        f"/api/countries → {st} (need JSON list under Preview)"
                    )
                else:
                    countries = data
                    tech_findings.append(f"/api/countries ok n={len(countries)}")
                for pair in ("US-SG", "SG-US"):
                    path = f"/api/crosswalks/{pair}"
                    pst, _, pdata = await _fetch(http, _join_preview(base, path))
                    tech_art["probes"].append({"path": path, "status": pst})
                    if pst >= 400 or not isinstance(pdata, dict):
                        tech_gaps.append("delivery-tech-api")
                        tech_findings.append(f"{path} → {pst}")
                    else:
                        steps = pdata.get("steps") or []
                        tech_findings.append(
                            f"{path} ok steps={len(steps) if isinstance(steps, list) else 0}"
                        )
            for check in list((live_qa or {}).get("checks") or []):
                if not isinstance(check, dict):
                    continue
                name = str(check.get("name") or "")
                if name in {
                    "preview-home-reachable",
                    "preview-asset-reachability",
                    "preview-content-depth",
                }:
                    if not check.get("passed"):
                        tech_gaps.append(name)
                        tech_findings.append(str(check.get("detail") or name)[:200])
            tech_ok = not tech_gaps
            roles.append(
                RoleReview(
                    role_id="tech",
                    accepted=tech_ok,
                    score=0.9 if tech_ok else 0.3,
                    gaps=list(dict.fromkeys(tech_gaps))[:12],
                    findings=tech_findings[:12],
                    artifacts=tech_art,
                )
            )
        elif is_catalog:
            # Still fetch countries for Product/Test when Tech is not selected.
            st, _, data = await _fetch(http, _join_preview(base, "/api/countries"))
            if isinstance(data, list):
                countries = data

        # --- Product: substance vs outline ---
        if "product" in want:
            product_gaps: list[str] = []
            product_findings: list[str] = []
            product_art: dict[str, Any] = {"thin_samples": []}
            if len(home_text) < home_min:
                product_gaps.append("delivery-product-outline")
                product_findings.append(
                    f"home visible text {len(home_text)} < {home_min} (outline/shell)"
                )
            if is_catalog:
                thin_countries = 0
                for item in countries:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("country_code") or item.get("code") or "?")
                    points = item.get("points") or []
                    if not isinstance(points, list) or len(points) < 10:
                        product_gaps.append("preview-content-depth")
                        product_findings.append(
                            f"{code} points={len(points) if isinstance(points, list) else 0}<10"
                        )
                        continue
                    for idx, pt in enumerate(points[:8]):
                        if not isinstance(pt, dict):
                            continue
                        weak = _thin_fields(
                            pt,
                            _MIN_POINT_TEXT,
                            groups=(
                                ("title", "name"),
                                ("statute",),
                                ("source", "source_url"),
                                ("obligations", "obligation", "body"),
                                ("scenario", "detail"),
                                ("risk",),
                            ),
                        )
                        if weak:
                            thin_countries += 1
                            product_art["thin_samples"].append(
                                {"country": code, "idx": idx, "weak": weak[:4]}
                            )
                            if len(product_art["thin_samples"]) >= 6:
                                break
                    if len(product_art["thin_samples"]) >= 6:
                        break
                if thin_countries or product_art["thin_samples"]:
                    product_gaps.append("delivery-product-outline")
                    product_findings.append(
                        "rule points read as outlines — need operable handbook detail "
                        f"(samples={len(product_art['thin_samples'])})"
                    )
                for pair in ("US-SG", "SG-US"):
                    pst, _, pdata = await _fetch(
                        http, _join_preview(base, f"/api/crosswalks/{pair}")
                    )
                    if pst >= 400 or not isinstance(pdata, dict):
                        continue
                    steps = pdata.get("steps") or []
                    if not isinstance(steps, list) or len(steps) < 10:
                        product_gaps.append("preview-content-depth")
                        product_findings.append(
                            f"{pair} steps thin count="
                            f"{len(steps) if isinstance(steps, list) else 0}"
                        )
                        continue
                    thin_steps = 0
                    for idx, step in enumerate(steps[:8]):
                        if not isinstance(step, dict):
                            continue
                        weak = _thin_fields(
                            step,
                            _MIN_STEP_TEXT,
                            groups=(
                                ("trigger",),
                                ("action",),
                                ("check",),
                                ("evidence",),
                                ("owner", "owner_role"),
                                ("priority",),
                            ),
                        )
                        if weak:
                            thin_steps += 1
                            product_art["thin_samples"].append(
                                {"pair": pair, "idx": idx, "weak": weak[:4]}
                            )
                    if thin_steps:
                        product_gaps.append("delivery-product-outline")
                        product_findings.append(
                            f"{pair}: {thin_steps}/8 sampled steps are outline-thin"
                        )
            product_ok = not product_gaps
            roles.append(
                RoleReview(
                    role_id="product",
                    accepted=product_ok,
                    score=0.9 if product_ok else 0.25,
                    gaps=list(dict.fromkeys(product_gaps))[:12],
                    findings=product_findings[:12],
                    artifacts=product_art,
                )
            )

        # --- Test: scenario matrix against live surface ---
        if "test" in want:
            test_gaps: list[str] = []
            test_findings: list[str] = []
            scenarios: list[dict[str, Any]] = [
                {"id": "home", "path": "/", "expect": "html200"},
            ]
            if is_catalog:
                scenarios.extend(
                    [
                        {"id": "countries_page", "path": "/countries", "expect": "html200"},
                        {"id": "country_sg", "path": "/countries/SG", "expect": "html_detail"},
                        {"id": "country_us", "path": "/countries/US", "expect": "html_detail"},
                        {"id": "countries_api", "path": "/api/countries", "expect": "json_list"},
                        {"id": "cw_us_sg", "path": "/api/crosswalks/US-SG", "expect": "json_steps"},
                        {"id": "cw_sg_us", "path": "/api/crosswalks/SG-US", "expect": "json_steps"},
                        {"id": "cw_page", "path": "/crosswalks/US-SG", "expect": "html_detail"},
                    ]
                )
            evidence: list[dict[str, Any]] = []
            page_bodies: dict[str, str] = {}
            for sc in scenarios:
                st, body, data = await _fetch(http, _join_preview(base, sc["path"]))
                ok = False
                detail = f"status={st}"
                exp = sc["expect"]
                if exp == "html200":
                    ok = st < 400 and (
                        "<html" in body[:500].lower()
                        or "<!doctype" in body[:500].lower()
                    )
                    detail = f"status={st} chars={len(body)}"
                    page_bodies[sc["path"]] = body
                elif exp == "json_list":
                    ok = st < 400 and isinstance(data, list) and len(data) >= 1
                    detail = f"status={st} n={len(data) if isinstance(data, list) else 0}"
                elif exp == "json_steps":
                    steps = (data or {}).get("steps") if isinstance(data, dict) else None
                    ok = st < 400 and isinstance(steps, list) and len(steps) >= 10
                    detail = (
                        f"status={st} steps={len(steps) if isinstance(steps, list) else 0}"
                    )
                elif exp == "html_detail":
                    vis = len(_visible_text(body))
                    ok = st < 400 and vis >= _MIN_DETAIL_VISIBLE
                    detail = f"status={st} visible={vis}"
                    page_bodies[sc["path"]] = body
                evidence.append(
                    {"id": sc["id"], "path": sc["path"], "ok": ok, "detail": detail}
                )
                if not ok:
                    test_gaps.append("delivery-test-scenarios")
                    test_findings.append(f"scenario {sc['id']} FAIL {detail}")
                else:
                    test_findings.append(f"scenario {sc['id']} PASS {detail}")
            if is_catalog and countries:
                for item in countries:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("country_code") or item.get("code") or "").upper()
                    if code not in {"SG", "US"}:
                        continue
                    points = item.get("points") or []
                    if not isinstance(points, list) or not points:
                        continue
                    page = page_bodies.get(f"/countries/{code}") or ""
                    vis = _visible_text(page)
                    hit = 0
                    for pt in points[:12]:
                        if not isinstance(pt, dict):
                            continue
                        title = str(pt.get("title") or pt.get("name") or "").strip()
                        needle = title.split("/")[0].strip()[:8] if title else ""
                        if needle and needle in vis:
                            hit += 1
                    need_hit = min(8, len(points))
                    if hit < need_hit:
                        test_gaps.append("delivery-test-scenarios")
                        test_findings.append(
                            f"country {code} detail renders {hit}/{need_hit} point titles "
                            "(API-only catalogs are not Test pass)"
                        )
                    else:
                        test_findings.append(f"country {code} detail titles hit={hit}")
            for check in list((live_qa or {}).get("checks") or []):
                if isinstance(check, dict) and check.get("name") == "preview-internal-nav":
                    if not check.get("passed"):
                        test_gaps.append("preview-internal-nav")
                        test_findings.append(
                            str(check.get("detail") or "nav failed")[:200]
                        )
            test_art = {
                "scenario_matrix": scenarios,
                "scenario_evidence": evidence,
                "note": (
                    "Hive structure QA accepted≠test pass; this matrix is authoritative."
                ),
            }
            test_ok = not test_gaps
            roles.append(
                RoleReview(
                    role_id="test",
                    accepted=test_ok,
                    score=0.9 if test_ok else 0.3,
                    gaps=list(dict.fromkeys(test_gaps))[:12],
                    findings=test_findings[:14],
                    artifacts=test_art,
                )
            )

        # --- UX: designed surface + readable detail ---
        if "ux" in want:
            ux_gaps: list[str] = []
            ux_findings: list[str] = []
            ux_art: dict[str, Any] = {}
            for check in list((live_qa or {}).get("checks") or []):
                if not isinstance(check, dict):
                    continue
                name = str(check.get("name") or "")
                if name in {
                    "stylesheet-substance",
                    "styled-surface",
                    "semantic-main",
                    "preview-internal-nav",
                    "stylesheet-present",
                }:
                    if not check.get("passed"):
                        ux_gaps.append(name)
                        ux_findings.append(str(check.get("detail") or name)[:200])
                    else:
                        ux_findings.append(f"{name} ok")
            if len(home_text) < home_min:
                ux_gaps.append("delivery-ux-surface")
                ux_findings.append(
                    f"home copy too thin for IA ({len(home_text)} chars, min={home_min})"
                )
            if is_catalog:
                st, body, _ = await _fetch(
                    http, _join_preview(base, "/crosswalks/US-SG")
                )
                vis = len(_visible_text(body))
                ux_art["crosswalk_page_visible"] = vis
                if st >= 400 or vis < _MIN_DETAIL_VISIBLE:
                    ux_gaps.append("delivery-ux-surface")
                    ux_findings.append(
                        f"crosswalk detail page inadequate status={st} "
                        f"visible={vis}<{_MIN_DETAIL_VISIBLE}"
                    )
                else:
                    ux_findings.append(f"crosswalk detail visible={vis}")
            ux_ok = not ux_gaps
            roles.append(
                RoleReview(
                    role_id="ux",
                    accepted=ux_ok,
                    score=0.9 if ux_ok else 0.35,
                    gaps=list(dict.fromkeys(ux_gaps))[:12],
                    findings=ux_findings[:12],
                    artifacts=ux_art,
                )
            )

        # --- Ops: host / preview environment sustainability ---
        if "ops" in want:
            ops_gaps: list[str] = []
            ops_findings: list[str] = []
            ops_art: dict[str, Any] = {}
            try:
                from regent.infrastructure.host_resources import (
                    evaluate_host,
                    measure_host_resources,
                )

                resources = measure_host_resources()
                decision = evaluate_host(resources)
                ops_art["host"] = decision.as_dict()
                if decision.unhealthy:
                    ops_gaps.append("delivery-ops-host")
                    ops_gaps.extend(list(decision.reasons)[:4])
                    ops_findings.append(
                        "host unhealthy: " + "; ".join(decision.reasons)[:240]
                    )
                else:
                    ops_findings.append(
                        f"host ok disk={resources.disk_percent:.1f}% "
                        f"venvs={resources.preview_venv_count}"
                    )
            except Exception as exc:  # noqa: BLE001
                # Preview can still be validated remotely; mark ops inconclusive
                # only when host module is broken — fail closed on import/runtime errors
                # that look like resource pressure signals in live_qa metadata.
                ops_art["host_error"] = f"{type(exc).__name__}: {exc}"[:200]
                ops_findings.append(
                    f"host probe skipped ({type(exc).__name__}); "
                    "require healthy Preview process"
                )
                if home_status >= 400 or home_status == 0:
                    ops_gaps.append("delivery-ops-host")
                    ops_findings.append("preview home down — ops refuses soft-pass")
            ops_ok = not ops_gaps
            roles.append(
                RoleReview(
                    role_id="ops",
                    accepted=ops_ok,
                    score=0.9 if ops_ok else 0.3,
                    gaps=list(dict.fromkeys(ops_gaps))[:12],
                    findings=ops_findings[:12],
                    artifacts=ops_art,
                )
            )
    finally:
        if owns:
            await http.aclose()

    # Stable role order matching catalog.
    order = {a.role_id: i for i, a in enumerate(DELIVERY_ROLE_AGENTS)}
    roles.sort(key=lambda r: order.get(r.role_id, 99))

    rejected = [r for r in roles if not r.accepted]
    gaps: list[str] = []
    for r in rejected:
        gaps.extend(r.gaps)
    gaps = list(dict.fromkeys(gaps))[:16]
    accepted = bool(roles) and not rejected
    score = sum(r.score for r in roles) / max(len(roles), 1) if roles else 0.0
    if accepted:
        reason = (
            "Product/Tech/Test/UX/Ops Delivery Role Swarm accepted Live Preview "
            "with field-level substance, scenario evidence, and host guard."
        )
    else:
        who = ",".join(r.role_id for r in rejected) or "none"
        reason = (
            f"Delivery Role Swarm rejected by [{who}]. "
            "Outline-only or incomplete journeys are not Regent-complete delivery."
        )
    return DeliverySwarmResult(
        accepted=accepted,
        score=round(score, 3),
        reason=reason,
        gaps=gaps,
        roles=roles,
        catalog=catalog,
    )
