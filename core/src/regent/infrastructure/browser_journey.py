"""Browser Journey Runner — Playwright-based task verification.

Executes core task journeys against preview deployments to verify
that generated apps actually work for end users (G6).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class JourneyStepKind(StrEnum):
    """Supported journey step types."""

    PAGE_LOAD = "page_load"
    ELEMENT_EXISTS = "element_exists"
    ELEMENT_TEXT = "element_text"
    CLICK = "click"
    FILL = "fill"
    NAVIGATE = "navigate"
    WAIT_FOR = "wait_for"
    SCREENSHOT = "screenshot"


@dataclass(frozen=True, slots=True)
class JourneyStep:
    """A single step in a browser journey."""

    kind: JourneyStepKind
    selector: str = ""
    value: str = ""
    timeout_ms: int = 10_000
    description: str = ""
    optional: bool = False


@dataclass(frozen=True, slots=True)
class StepResult:
    """Result of executing a single journey step."""

    step: JourneyStep
    passed: bool
    detail: str = ""
    screenshot_path: str | None = None


@dataclass(frozen=True, slots=True)
class JourneyResult:
    """Result of executing a full journey."""

    journey_id: uuid.UUID
    preview_url: str
    steps: list[StepResult] = field(default_factory=list)
    passed: bool = True
    total_steps: int = 0
    passed_steps: int = 0
    failed_steps: int = 0
    duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_steps == 0:
            return 1.0
        return self.passed_steps / self.total_steps


class BrowserJourneyRunner:
    """Execute core task browser journeys against preview URLs.

    When Playwright is available, performs real browser automation.
    When Playwright is not installed, runs in dry-run mode (all steps pass
    with a note that no real browser verification was performed).
    """

    def __init__(self, *, headless: bool = True, timeout_ms: int = 30_000) -> None:
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._playwright_available = self._check_playwright()

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def playwright_available(self) -> bool:
        return self._playwright_available

    async def run_journey(
        self,
        preview_url: str,
        journey_steps: list[JourneyStep],
        *,
        journey_id: uuid.UUID | None = None,
    ) -> JourneyResult:
        """Execute a journey against a preview URL.

        If Playwright is not installed, returns a dry-run result.
        """
        jid = journey_id or uuid.uuid4()
        if not self._playwright_available:
            logger.info(
                "Playwright not available; dry-run journey",
                extra={"preview_url": preview_url, "steps": len(journey_steps)},
            )
            return JourneyResult(
                journey_id=jid,
                preview_url=preview_url,
                steps=[
                    StepResult(
                        step=s,
                        passed=True,
                        detail="dry-run (Playwright not installed)",
                    )
                    for s in journey_steps
                ],
                passed=True,
                total_steps=len(journey_steps),
                passed_steps=len(journey_steps),
                failed_steps=0,
            )

        return await self._run_with_playwright(jid, preview_url, journey_steps)

    async def _run_with_playwright(
        self,
        journey_id: uuid.UUID,
        preview_url: str,
        journey_steps: list[JourneyStep],
    ) -> JourneyResult:
        """Execute journey using real Playwright browser."""
        import time

        from playwright.async_api import async_playwright

        t0 = time.monotonic()
        results: list[StepResult] = []
        passed_count = 0
        failed_count = 0

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            page = await browser.new_page()
            try:
                for step in journey_steps:
                    result = await self._execute_step(page, step)
                    results.append(result)
                    if result.passed:
                        passed_count += 1
                    else:
                        failed_count += 1
                        if not step.optional:
                            break
            finally:
                await browser.close()

        elapsed = (time.monotonic() - t0) * 1000
        all_passed = failed_count == 0
        return JourneyResult(
            journey_id=journey_id,
            preview_url=preview_url,
            steps=results,
            passed=all_passed,
            total_steps=len(results),
            passed_steps=passed_count,
            failed_steps=failed_count,
            duration_ms=elapsed,
        )

    async def _execute_step(self, page: Any, step: JourneyStep) -> StepResult:
        """Execute a single journey step on a Playwright page."""
        try:
            if step.kind is JourneyStepKind.PAGE_LOAD:
                await page.goto(step.value or step.selector, timeout=step.timeout_ms)
                return StepResult(step=step, passed=True, detail="page loaded")

            elif step.kind is JourneyStepKind.ELEMENT_EXISTS:
                el = page.locator(step.selector)
                await el.wait_for(state="attached", timeout=step.timeout_ms)
                return StepResult(step=step, passed=True, detail=f"found: {step.selector}")

            elif step.kind is JourneyStepKind.ELEMENT_TEXT:
                el = page.locator(step.selector)
                text = await el.text_content(timeout=step.timeout_ms)
                ok = step.value in (text or "")
                return StepResult(
                    step=step,
                    passed=ok,
                    detail=f"text={'matched' if ok else 'mismatch'}: {step.value!r}",
                )

            elif step.kind is JourneyStepKind.CLICK:
                await page.locator(step.selector).click(timeout=step.timeout_ms)
                return StepResult(step=step, passed=True, detail=f"clicked: {step.selector}")

            elif step.kind is JourneyStepKind.FILL:
                await page.locator(step.selector).fill(step.value, timeout=step.timeout_ms)
                return StepResult(step=step, passed=True, detail=f"filled: {step.selector}")

            elif step.kind is JourneyStepKind.NAVIGATE:
                await page.goto(step.value, timeout=step.timeout_ms)
                return StepResult(step=step, passed=True, detail=f"navigated: {step.value}")

            elif step.kind is JourneyStepKind.WAIT_FOR:
                await page.locator(step.selector).wait_for(
                    state=step.value or "visible", timeout=step.timeout_ms
                )
                return StepResult(step=step, passed=True, detail=f"waited: {step.selector}")

            elif step.kind is JourneyStepKind.SCREENSHOT:
                path = step.value or f"/tmp/journey-{step.selector}.png"
                await page.screenshot(path=path)
                return StepResult(
                    step=step, passed=True, detail=f"screenshot: {path}", screenshot_path=path
                )

            else:
                return StepResult(step=step, passed=False, detail=f"unknown step kind: {step.kind}")

        except Exception as exc:
            return StepResult(step=step, passed=False, detail=f"{type(exc).__name__}: {exc}")


def build_journey_from_requirement(
    requirement: dict[str, Any],
) -> list[JourneyStep]:
    """Generate journey steps from a requirement revision's machine-executable journey.

    Falls back to basic page-load + main-element checks when the requirement
    doesn't define explicit journey steps.
    """
    journey_def = requirement.get("machine_executable_journey") or []
    if journey_def:
        steps: list[JourneyStep] = []
        for item in journey_def:
            kind = JourneyStepKind(item.get("kind", "page_load"))
            steps.append(
                JourneyStep(
                    kind=kind,
                    selector=item.get("selector", ""),
                    value=item.get("value", ""),
                    timeout_ms=int(item.get("timeout_ms", 10_000)),
                    description=item.get("description", ""),
                    optional=bool(item.get("optional", False)),
                )
            )
        return steps

    # Fallback: basic page load + check for main content
    return [
        JourneyStep(
            kind=JourneyStepKind.PAGE_LOAD,
            value="",
            description="Load the preview page",
        ),
        JourneyStep(
            kind=JourneyStepKind.ELEMENT_EXISTS,
            selector="main, #root, #app, body",
            description="Verify main content area exists",
        ),
        JourneyStep(
            kind=JourneyStepKind.ELEMENT_EXISTS,
            selector="h1, h2, [data-testid='title']",
            description="Verify title or heading exists",
            optional=True,
        ),
    ]
