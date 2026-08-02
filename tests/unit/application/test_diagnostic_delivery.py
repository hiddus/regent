"""Unit tests for DiagnosticDelivery builder."""

from __future__ import annotations

from pathlib import Path

from regent.application.diagnostic_delivery import (
    build_diagnostic_delivery,
    public_diagnostic_delivery,
)


def test_build_diagnostic_delivery_from_sandbox(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "index.html").write_text(
        "<main><h1>hello</h1><p>" + ("说明" * 40) + "</p></main>",
        encoding="utf-8",
    )
    (sandbox / ".regent_budget_exhausted.json").write_text(
        '{"primary_failure_code":"BUDGET_EXHAUSTED","ledger":{"turns_used":40,"turns_limit":40}}',
        encoding="utf-8",
    )
    dest = tmp_path / "ws"
    dest.mkdir()

    payload = build_diagnostic_delivery(
        goal_id="11111111-1111-1111-1111-111111111111",
        terminal_reason="BUDGET_EXHAUSTED",
        reasons=["BUDGET_EXHAUSTED: turns"],
        draft_uri=sandbox.as_uri(),
        workspace_root=dest,
    )
    public = public_diagnostic_delivery(payload)

    assert public["promote_allowed"] is False
    assert public["terminal_reason"] == "BUDGET_EXHAUSTED"
    assert public["resumable"] is True
    assert public["resume"]["base_snapshot_id"]
    assert any(a["kind"] == "source_snapshot" for a in public["artifacts"])
    assert "_snapshot_uri" not in public
    assert public["preview"]["state"] == "UNAVAILABLE"
    assert any(r["action"] == "CONTINUE_FROM_SNAPSHOT" for r in public["recommendations"])


def test_classify_budget_exhausted_gap_kind() -> None:
    from regent.application.delivery_gap_recovery import classify_delivery_gap_kind

    assert classify_delivery_gap_kind(["BUDGET_EXHAUSTED: turns"]) == "BUDGET_EXHAUSTED"
