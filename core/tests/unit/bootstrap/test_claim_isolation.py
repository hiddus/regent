"""Claim isolation and novel worker assembly smoke tests."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from regent.bootstrap.event_scope import claimable_event_types
from regent.bootstrap.service_mode import ServiceMode
from regent.novel.application.directing_protocol import is_directed, production_protocol
from regent.novel.application.editorial_repair import (
    resolve_editorial_mode_for_work,
    work_in_auto_canary,
)
from regent.runtime.dispatcher import OutboxDispatcher, claim_statement
from types import SimpleNamespace


def test_novel_claim_excludes_legacy_events() -> None:
    novel = claimable_event_types(ServiceMode.NOVEL)
    legacy = claimable_event_types(ServiceMode.LEGACY)
    assert novel == frozenset()
    assert legacy is not None
    assert "GoalExecutionRequested" in legacy
    assert "TimerFired" in legacy
    # Novel SQL claims nothing even if legacy events are pending.
    sql = str(
        claim_statement(10, event_types=novel).compile(dialect=postgresql.dialect())
    ).lower()
    assert "false" in sql or "1 != 1" in sql


def test_legacy_claim_filters_to_known_types() -> None:
    legacy = claimable_event_types(ServiceMode.LEGACY)
    assert legacy is not None
    sql = str(
        claim_statement(5, event_types=legacy).compile(dialect=postgresql.dialect())
    ).lower()
    assert "event_type" in sql


def test_dispatcher_stores_event_type_scope() -> None:
    d = OutboxDispatcher(
        sessions=None,  # type: ignore[arg-type]
        handlers={},
        event_types=frozenset({"TimerFired"}),
    )
    assert d._event_types == frozenset({"TimerFired"})


def test_auto_canary_closed_forces_shadow(monkeypatch) -> None:
    monkeypatch.setenv("REGENT_EDITOR_REPAIR_MODE", "auto")
    monkeypatch.setenv("REGENT_EDITOR_REPAIR_AUTO_PERCENT", "0")
    from regent.config import get_settings

    get_settings.cache_clear()
    # Settings may still use defaults if env prefix needs REGENT_
    mode = resolve_editorial_mode_for_work(
        "11111111-1111-1111-1111-111111111111", raw="auto"
    )
    # With percent from settings default 0, auto demotes to shadow
    assert mode == "shadow"
    assert work_in_auto_canary("11111111-1111-1111-1111-111111111111") is False


def test_auto_canary_full_open(monkeypatch) -> None:
    from regent.novel.application import editorial_repair as er

    monkeypatch.setattr(er, "_auto_percent", lambda: 100)
    assert resolve_editorial_mode_for_work("any-work", raw="auto") == "auto"


def test_directing_protocol_defaults() -> None:
    run = SimpleNamespace(generation_context={"architecture_version": "director_v2"})
    assert is_directed(run)
    assert production_protocol(run) == "scene"
    assert production_protocol({"protocol": "script_scene"}) == "script_scene"
