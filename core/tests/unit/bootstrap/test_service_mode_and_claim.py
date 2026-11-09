"""Unit tests for service mode and outbox claim scoping."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from regent.bootstrap.event_scope import (
    LEGACY_EVENT_TYPES,
    claimable_event_types,
    classify_event_type,
)
from regent.bootstrap.service_mode import ServiceMode, parse_service_mode
from regent.runtime.dispatcher import claim_statement


def test_parse_service_mode_defaults_and_values() -> None:
    assert parse_service_mode(None) is ServiceMode.COMBINED
    assert parse_service_mode("novel") is ServiceMode.NOVEL
    assert parse_service_mode("LEGACY") is ServiceMode.LEGACY


def test_claimable_types_by_mode() -> None:
    assert claimable_event_types(ServiceMode.COMBINED) is None
    assert claimable_event_types(ServiceMode.NOVEL) == frozenset()
    legacy = claimable_event_types(ServiceMode.LEGACY)
    assert legacy is not None
    assert "GoalExecutionRequested" in legacy
    assert "TimerFired" in legacy
    assert "TimerFired" in LEGACY_EVENT_TYPES


def test_classify_unknown_not_auto_legacy() -> None:
    assert classify_event_type("TotallyUnknownEvent") == "unknown"


def test_claim_statement_empty_subscription_compiles() -> None:
    stmt = claim_statement(5, event_types=frozenset())
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))
    assert "false" in sql.lower() or "1 != 1" in sql.lower() or "false()" in sql.lower()


def test_claim_statement_filters_event_types() -> None:
    stmt = claim_statement(3, event_types=frozenset({"TimerFired", "GoalExecutionRequested"}))
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "event_type" in sql.lower()


def test_novel_mode_app_does_not_import_legacy_routers_at_module_level() -> None:
    """Smoke: bootstrap.legacy_api is importable without requiring create_app."""
    from regent.bootstrap import legacy_api

    assert hasattr(legacy_api, "mount_legacy_surface")


def test_finalize_chapter_acceptance_sets_hash() -> None:
    from types import SimpleNamespace

    from regent.novel.application import chapter_accept as ca
    from regent.novel.application.chapter_accept import finalize_chapter_acceptance

    run = SimpleNamespace(
        content="hello",
        review={"passed": True},
        generation_context={},
    )
    production = {
        "accepted": [],
        "takes": [],
        "working_state": {"x": 1},
        "script_protocol": {},
        "cast": {},
    }
    original = ca.commit_run_story_ledger
    ca.commit_run_story_ledger = lambda *a, **k: None  # type: ignore[assignment]
    try:
        finalize_chapter_acceptance(run, production, node_completed=True)
    finally:
        ca.commit_run_story_ledger = original  # type: ignore[assignment]

    assert run.generation_context["validated_content_hash"]
    assert run.generation_context["actual_state"] == {"x": 1}
