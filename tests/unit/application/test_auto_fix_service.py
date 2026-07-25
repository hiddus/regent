"""Unit tests for AutoFixService."""

import pytest
from regent.application.auto_fix_service import AutoFixService


def test_fix_adds_main_wrapper() -> None:
    """Test that auto-fix adds <main> tag when missing."""
    html = "<html><body><p>Hello</p></body></html>"
    service = AutoFixService()
    result = service.fix(
        html,
        failed_checks=[
            type("Check", (), {"name": "semantic-main", "passed": False, "detail": "missing"})()
        ],
    )
    assert "<main" in result.html.lower()
    assert result.fixes_applied


def test_fix_adds_data_regent_event() -> None:
    """Test that auto-fix adds data-regent-event attribute."""
    html = "<html><body><button>Click</button></body></html>"
    service = AutoFixService()
    result = service.fix(
        html,
        failed_checks=[
            type("Check", (), {"name": "observation-hook", "passed": False, "detail": "missing"})()
        ],
    )
    assert "data-regent-event" in result.html
    assert result.fixes_applied


def test_fix_injects_css() -> None:
    """Test that auto-fix injects CSS when missing."""
    html = "<html><head></head><body><main><p>Hello</p></main></body></html>"
    service = AutoFixService()
    result = service.fix(
        html,
        failed_checks=[
            type("Check", (), {"name": "stylesheet-present", "passed": False, "detail": "missing"})()
        ],
    )
    assert "<style>" in result.html
    assert result.fixes_applied


def test_fix_removes_demo_content() -> None:
    """Test that auto-fix removes demo/placeholder text."""
    html = "<html><body><main><p>Lorem ipsum dolor sit amet</p></main></body></html>"
    service = AutoFixService()
    result = service.fix(
        html,
        failed_checks=[
            type("Check", (), {"name": "forbid-demo-copy", "passed": False, "detail": "demo detected"})()
        ],
    )
    assert "lorem ipsum" not in result.html.lower()
    assert result.fixes_applied


def test_fix_enhances_deliverable_content() -> None:
    """Test that auto-fix adds deliverable keywords."""
    html = "<html><body><main><p>Some content</p></main></body></html>"
    service = AutoFixService()
    result = service.fix(
        html,
        acceptance_contract={"first_deliverable": "AI news aggregator"},
        failed_checks=[
            type("Check", (), {"name": "goal-first-deliverable", "passed": False, "detail": "keywords missing"})()
        ],
    )
    assert "ai" in result.html.lower() or "news" in result.html.lower()
    assert result.fixes_applied


def test_fix_multiple_issues() -> None:
    """Test that auto-fix handles multiple failed checks."""
    html = "<html><body><p>Lorem ipsum</p></body></html>"
    service = AutoFixService()
    result = service.fix(
        html,
        acceptance_contract={"first_deliverable": "test app"},
        failed_checks=[
            type("Check", (), {"name": "semantic-main", "passed": False, "detail": "missing"})(),
            type("Check", (), {"name": "stylesheet-present", "passed": False, "detail": "missing"})(),
            type("Check", (), {"name": "forbid-demo-copy", "passed": False, "detail": "demo"})(),
        ],
    )
    assert "<main" in result.html.lower()
    assert "<style>" in result.html
    assert "lorem ipsum" not in result.html.lower()
    assert len(result.fixes_applied) >= 3


def test_fix_returns_unfixed_when_cannot_fix() -> None:
    """Test that auto-fix reports when it cannot fix all issues."""
    # HTML that's too broken to fix
    html = "<html></html>"
    service = AutoFixService()
    result = service.fix(
        html,
        failed_checks=[
            type("Check", (), {"name": "semantic-main", "passed": False, "detail": "missing"})(),
        ],
    )
    # Should attempt fixes even if HTML is minimal
    assert result.attempts > 0
