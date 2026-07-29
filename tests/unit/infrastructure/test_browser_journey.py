"""Browser Journey Runner tests (G6).

Verifies:
- JourneyStep and JourneyResult value objects
- Dry-run mode when Playwright is not installed
- Journey building from requirement definitions
- Step execution logic
"""

from __future__ import annotations

import uuid

import pytest
from regent.infrastructure.browser_journey import (
    BrowserJourneyRunner,
    JourneyResult,
    JourneyStep,
    JourneyStepKind,
    StepResult,
    build_journey_from_requirement,
)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


def test_journey_step_creation() -> None:
    step = JourneyStep(
        kind=JourneyStepKind.PAGE_LOAD,
        value="https://example.com",
        description="Load page",
    )
    assert step.kind is JourneyStepKind.PAGE_LOAD
    assert step.timeout_ms == 10_000
    assert step.optional is False


def test_journey_result_success_rate() -> None:
    result = JourneyResult(
        journey_id=uuid.uuid4(),
        preview_url="http://localhost:3000",
        total_steps=4,
        passed_steps=3,
        failed_steps=1,
        passed=False,
    )
    assert result.success_rate == 0.75


def test_journey_result_no_steps() -> None:
    result = JourneyResult(
        journey_id=uuid.uuid4(),
        preview_url="http://localhost:3000",
    )
    assert result.success_rate == 1.0


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_journey_when_no_playwright() -> None:
    runner = BrowserJourneyRunner()
    if runner.playwright_available:
        pytest.skip("Playwright is installed; dry-run test not applicable")
    steps = [
        JourneyStep(kind=JourneyStepKind.PAGE_LOAD, value="http://localhost:3000"),
        JourneyStep(kind=JourneyStepKind.ELEMENT_EXISTS, selector="main"),
    ]
    result = await runner.run_journey("http://localhost:3000", steps)
    assert result.passed is True
    assert result.total_steps == 2
    assert result.passed_steps == 2
    # All steps should be dry-run
    for step_result in result.steps:
        assert "dry-run" in step_result.detail


@pytest.mark.asyncio
async def test_dry_run_with_empty_steps_list() -> None:
    runner = BrowserJourneyRunner()
    result = await runner.run_journey("http://localhost:3000", [])
    assert result.passed is True
    assert result.total_steps == 0


# ---------------------------------------------------------------------------
# Journey building from requirement
# ---------------------------------------------------------------------------


def test_build_journey_from_explicit_definition() -> None:
    requirement = {
        "machine_executable_journey": [
            {"kind": "page_load", "value": "http://localhost:3000"},
            {"kind": "element_exists", "selector": "h1"},
            {"kind": "click", "selector": "button#start"},
            {"kind": "wait_for", "selector": ".result", "value": "visible"},
        ]
    }
    steps = build_journey_from_requirement(requirement)
    assert len(steps) == 4
    assert steps[0].kind is JourneyStepKind.PAGE_LOAD
    assert steps[1].kind is JourneyStepKind.ELEMENT_EXISTS
    assert steps[2].kind is JourneyStepKind.CLICK
    assert steps[3].kind is JourneyStepKind.WAIT_FOR


def test_build_journey_fallback_when_no_definition() -> None:
    requirement = {"first_deliverable": "AI news list page"}
    steps = build_journey_from_requirement(requirement)
    assert len(steps) >= 2
    assert steps[0].kind is JourneyStepKind.PAGE_LOAD
    assert steps[1].kind is JourneyStepKind.ELEMENT_EXISTS


def test_build_journey_empty_requirement() -> None:
    steps = build_journey_from_requirement({})
    assert len(steps) >= 1
    assert steps[0].kind is JourneyStepKind.PAGE_LOAD


# ---------------------------------------------------------------------------
# Step kinds
# ---------------------------------------------------------------------------


def test_all_step_kinds_defined() -> None:
    assert JourneyStepKind.PAGE_LOAD == "page_load"
    assert JourneyStepKind.ELEMENT_EXISTS == "element_exists"
    assert JourneyStepKind.ELEMENT_TEXT == "element_text"
    assert JourneyStepKind.CLICK == "click"
    assert JourneyStepKind.FILL == "fill"
    assert JourneyStepKind.NAVIGATE == "navigate"
    assert JourneyStepKind.WAIT_FOR == "wait_for"
    assert JourneyStepKind.SCREENSHOT == "screenshot"


# ---------------------------------------------------------------------------
# Runner properties
# ---------------------------------------------------------------------------


def test_runner_playwright_check() -> None:
    runner = BrowserJourneyRunner()
    # In test environment, Playwright may or may not be installed
    assert isinstance(runner.playwright_available, bool)
