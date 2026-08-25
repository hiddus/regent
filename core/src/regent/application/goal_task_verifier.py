"""Goal-aware product verification (P1-2 audit fix).

Bridges the gap between proxy-metric QA (CSS substance, min_chars) and
actual task completion: checks whether the deployed product satisfies the
goal's stated success criteria.

Two layers:
1. LivePreviewQaResult — structural proxy checks (existing live_preview_qa).
2. GoalTaskVerdict — goal-specific task completion (this module).

Both must pass for a product to be considered verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class TaskCriterion:
    """One success-criterion check result."""

    label: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GoalTaskVerdict:
    """Result of goal-specific task completion verification."""

    passed: bool
    criteria: list[TaskCriterion] = field(default_factory=list)
    summary: str = ""
    skipped_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "criteria": [
                {"label": c.label, "passed": c.passed, "detail": c.detail} for c in self.criteria
            ],
            "skipped_reason": self.skipped_reason,
        }


# ── Keyword → success-criterion mapping ──────────────────────────────────
# Each entry: (regex matching criterion text, check function name).
_CRITERION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:页面|page|screen|视图).*(?:登录|login|sign.?in)", re.I), "has_login_page"),
    (
        re.compile(r"(?:页面|page|screen|视图).*(?:注册|register|sign.?up)", re.I),
        "has_register_page",
    ),
    (re.compile(r"(?:表单|form|输入|input).*(?:提交|submit)", re.I), "has_submit_form"),
    (re.compile(r"(?:列表|list|目录|catalog).*(?:展示|display|show)", re.I), "has_list_display"),
    (re.compile(r"(?:搜索|search|查询|find)", re.I), "has_search"),
    (re.compile(r"(?:响应式|responsive|mobile|移动端)", re.I), "has_responsive"),
    (re.compile(r"(?:导航|navigation|nav|菜单|menu)", re.I), "has_navigation"),
    (re.compile(r"(?:测试|test|单元测试|unit.?test)", re.I), "has_tests"),
    (re.compile(r"(?:API|接口|endpoint|路由|route)", re.I), "has_api_routes"),
    (re.compile(r"(?:数据|data|数据库|database|存储|storage)", re.I), "has_data_persistence"),
)


def _extract_criteria_labels(success_criteria: Any) -> list[tuple[str, str]]:
    """Parse success criteria text and return matched (criterion_text, check_name)."""
    if not success_criteria:
        return []
    if isinstance(success_criteria, list):
        blob = " ".join(str(c) for c in success_criteria)
    else:
        blob = str(success_criteria)
    matched: list[tuple[str, str]] = []
    for pattern, check_name in _CRITERION_PATTERNS:
        if pattern.search(blob):
            matched.append((pattern.pattern, check_name))
    return matched


async def verify_goal_task_completion(
    preview_url: str,
    success_criteria: Any,
    *,
    goal_input: str = "",
    timeout_seconds: float = 15.0,
) -> GoalTaskVerdict:
    """Check whether the deployed product satisfies goal-specific criteria.

    Unlike proxy metrics (CSS substance, min_chars), this verifies that the
    product actually has the features/pages the goal asked for.
    """
    if not preview_url or not preview_url.startswith(("http://", "https://")):
        return GoalTaskVerdict(
            passed=False,
            summary="no preview URL for task verification",
            skipped_reason="missing_preview_url",
        )

    criteria_map = _extract_criteria_labels(success_criteria)
    if not criteria_map:
        # Also scan goal_input for implicit requirements.
        criteria_map = _extract_criteria_labels(goal_input)
    if not criteria_map:
        return GoalTaskVerdict(
            passed=False,
            summary="success criteria could not be compiled into executable task checks",
            skipped_reason="no_criteria_matched",
        )

    # De-duplicate check names.
    check_names = list({name for _, name in criteria_map})

    http = httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True)
    results: list[TaskCriterion] = []
    try:
        # Fetch home page once.
        try:
            home = await http.get(preview_url)
            home_html = home.text if home.status_code < 400 else ""
        except Exception as exc:
            return GoalTaskVerdict(
                passed=False,
                summary=f"preview unreachable: {exc}"[:200],
                skipped_reason="preview_unreachable",
            )

        home_lower = home_html.lower()

        for check_name in check_names:
            result = await _run_task_check(
                http,
                preview_url,
                home_html,
                home_lower,
                check_name,
            )
            results.append(result)

        passed = all(c.passed for c in results)
        n_pass = sum(1 for c in results if c.passed)
        return GoalTaskVerdict(
            passed=passed,
            criteria=results,
            summary=f"task completion {n_pass}/{len(results)} criteria passed",
        )
    finally:
        await http.aclose()


async def _run_task_check(
    http: httpx.AsyncClient,
    base_url: str,
    home_html: str,
    home_lower: str,
    check_name: str,
) -> TaskCriterion:
    """Run a single task-specific check against the deployed product."""

    if check_name == "has_login_page":
        ok = any(kw in home_lower for kw in ("login", "sign-in", "signin", "登录"))
        if not ok:
            # Check for a login link.
            ok = bool(re.search(r'href=["\'][^"\']*(?:login|signin|sign-in)', home_lower))
        return TaskCriterion(
            "has_login_page", ok, "login page/link found" if ok else "no login surface detected"
        )

    if check_name == "has_register_page":
        ok = any(kw in home_lower for kw in ("register", "sign-up", "signup", "注册"))
        if not ok:
            ok = bool(re.search(r'href=["\'][^"\']*(?:register|signup|sign-up)', home_lower))
        return TaskCriterion(
            "has_register_page", ok, "register surface found" if ok else "no register surface"
        )

    if check_name == "has_submit_form":
        ok = "<form" in home_lower
        if not ok:
            ok = bool(re.search(r'<input[^>]+type=["\'](?:submit|button)', home_lower))
        return TaskCriterion(
            "has_submit_form", ok, "form/submit found" if ok else "no form element"
        )

    if check_name == "has_list_display":
        ok = home_lower.count("<article") >= 1 or home_lower.count("<li") >= 3
        if not ok:
            ok = bool(re.search(r'class=["\'][^"\']*(?:card|item|list|grid)', home_lower))
        return TaskCriterion(
            "has_list_display", ok, "list/card elements found" if ok else "no list surface"
        )

    if check_name == "has_search":
        ok = "<input" in home_lower and any(
            kw in home_lower
            for kw in ('type="search"', 'type="text"', 'placeholder="search', 'placeholder="搜索')
        )
        if not ok:
            ok = bool(re.search(r"(?:search|查询|搜索)", home_lower))
        return TaskCriterion("has_search", ok, "search input found" if ok else "no search surface")

    if check_name == "has_responsive":
        ok = "viewport" in home_lower and "meta" in home_lower
        if not ok:
            ok = bool(re.search(r"@media\s", home_lower))
        return TaskCriterion(
            "has_responsive", ok, "viewport/media query found" if ok else "no responsive signals"
        )

    if check_name == "has_navigation":
        ok = any(tag in home_lower for tag in ("<nav", 'role="navigation"'))
        if not ok:
            # Multiple internal links imply navigation.
            from regent.application.live_preview_qa import _pick_nav_candidates

            nav_urls = _pick_nav_candidates(home_html, base_url=base_url, limit=3)
            ok = len(nav_urls) >= 2
        return TaskCriterion(
            "has_navigation", ok, "navigation structure found" if ok else "no navigation"
        )

    if check_name == "has_tests":
        # Check for test files in the product — requires probing /tests or similar.
        for test_path in ("/tests", "/test", "/__tests__"):
            try:
                resp = await http.get(base_url.rstrip("/") + test_path)
                if resp.status_code < 400:
                    return TaskCriterion(
                        "has_tests", True, f"test directory reachable at {test_path}"
                    )
            except Exception:
                pass
        # Fallback: check home page for test references.
        ok = bool(re.search(r"(?:test|spec)\.(?:js|ts|py)", home_lower))
        return TaskCriterion("has_tests", ok, "test references found" if ok else "no test evidence")

    if check_name == "has_api_routes":
        ok = bool(re.search(r"(?:/api/|fetch\(|axios\.|xhr|endpoint)", home_lower))
        return TaskCriterion(
            "has_api_routes", ok, "API route references found" if ok else "no API evidence"
        )

    if check_name == "has_data_persistence":
        ok = any(
            kw in home_lower
            for kw in ("localstorage", "sessionstorage", "indexeddb", "数据库", "database")
        )
        if not ok:
            ok = bool(re.search(r"(?:/api/|fetch\(|post\(|put\(|delete\()", home_lower))
        return TaskCriterion(
            "has_data_persistence",
            ok,
            "persistence signals found" if ok else "no data persistence evidence",
        )

    return TaskCriterion(check_name, False, f"unsupported task check: {check_name}")
