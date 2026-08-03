"""O0–O4 oh-my-cli absorption unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from regent.application.agent_loop_exit import (
    apply_exit_to_metadata,
    build_ask_envelope,
    build_exit,
    evaluate_complete_allowed,
    progress_loop_detected,
    quarantine_payload,
    record_progress_attempt,
)
from regent.application.evidence_bundle import build_evidence_bundle, verify_evidence_bundle
from regent.application.extension_readiness import build_extension_readiness, runner_may_invoke
from regent.application.session_export import build_session_export
from regent.application.side_question import build_side_context, run_side_question
from regent.application.trust_posture import build_trust_posture, permission_impact
from regent.application.turn_checkpoint import (
    TurnImageCollector,
    apply_plan,
    append_checkpoint_to_metadata,
    build_turn_checkpoint,
    plan_undo,
)
from regent.application.workflow_presets import apply_workflow_preset, list_workflow_presets
from regent.application.doctor import run_doctor
from regent.application.agent_control import permission_ask_envelope


def test_evaluate_complete_blocks_soft_and_progress() -> None:
    assert evaluate_complete_allowed("success")["safe"] is True
    assert evaluate_complete_allowed("soft_verify")["safe"] is False
    assert evaluate_complete_allowed("budget_exhausted")["blocker"] == "budget_exhausted"
    meta = apply_exit_to_metadata(
        {},
        build_exit(
            exit_kind="ASK_HUMAN",
            stop_reason="need_input",
            ask_envelope=build_ask_envelope(question="?", why_blocked="x"),
        ),
    )
    assert evaluate_complete_allowed("success", metadata=meta)["safe"] is False


def test_progress_loop_detector() -> None:
    meta: dict = {}
    for _ in range(3):
        meta, warning = record_progress_attempt(meta, item_key="item-a", threshold=3)
    assert warning["loop_detected"] is True
    assert progress_loop_detected(meta) is True


def test_permission_impact_and_envelope() -> None:
    impact = permission_impact(
        tool_name="write_file", arguments={"path": "src/app.py", "content": "x"}
    )
    assert impact["paths"] == ["src/app.py"]
    assert impact["effect_class"] == "workspace_mutate"
    env = permission_ask_envelope(
        tool_name="write_file",
        arguments={"path": "src/app.py"},
        execution_mode="ask",
    )
    assert env["paths"] == ["src/app.py"]
    assert env["impact"]["command_class"] == "file_write"


def test_trust_posture_levels() -> None:
    restricted = build_trust_posture({}, workspace_trusted=False, sandbox_enforced=True)
    assert restricted["level"] == "restricted"
    elevated = build_trust_posture({"execution_mode": "act"}, workspace_trusted=True, sandbox_enforced=True)
    assert elevated["level"] == "elevated"
    standard = build_trust_posture({"execution_mode": "ask"})
    assert standard["level"] == "standard"


@pytest.mark.asyncio
async def test_side_question_isolation() -> None:
    result = await run_side_question(
        question="what test runner?",
        context_messages=[{"role": "user", "content": "build app"}],
    )
    assert result["mutated_goal"] is False
    assert result["mutated_work_plan"] is False
    assert result["tools_invoked"] is False
    ctx = build_side_context([{"role": "user", "content": "x" * 5000}], max_chars=100)
    assert ctx["messages"][0]["content"].endswith("…")


def test_turn_undo_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("before", encoding="utf-8")
    collector = TurnImageCollector()
    collector.capture(target)
    target.write_text("after", encoding="utf-8")
    cp = build_turn_checkpoint(
        collector,
        workspace_root=root,
        session_id="s1",
        turn_index=1,
    )
    assert cp is not None
    meta = append_checkpoint_to_metadata({}, cp)
    plan = plan_undo(meta, workspace_root=root)
    assert plan["ok"] is True
    meta2, receipt = apply_plan(meta, plan, workspace_root=root)
    assert target.read_text(encoding="utf-8") == "before"
    assert receipt["op"] == "undo"
    assert meta2["turn_checkpoint_log"]["undone_turn_index"] == 1


def test_evidence_bundle_verify() -> None:
    meta = apply_exit_to_metadata(
        {},
        build_exit(
            exit_kind="COMPLETE",
            stop_reason="verified_pass",
            result_bundle={"summary": "done", "open_items": ["x"]},
        ),
    )
    bundle = build_evidence_bundle(meta, goal_id="g1")
    assert verify_evidence_bundle(bundle)["ok"] is True
    bundle["summary"] = "tampered"
    assert verify_evidence_bundle(bundle)["ok"] is False


def test_workflow_presets_and_doctor() -> None:
    presets = list_workflow_presets()
    assert any(p["name"] == "plan-build-qa" for p in presets)
    meta = apply_workflow_preset({}, "plan-build-qa")
    assert meta["workflow_preset"]["stages"][-1] == "qa"
    report = run_doctor(db_ok=True, delivery_review_seeded=True, canary_percent=5.0)
    assert report["ok"] is True
    readiness = build_extension_readiness(
        [{"name": "delivery-review-v1", "certified": True, "available": True}]
    )
    assert runner_may_invoke(readiness, "delivery-review-v1")
    assert not runner_may_invoke(readiness, "missing")


def test_session_export_redacts_secrets() -> None:
    export = build_session_export(
        goal_id="g1",
        metadata={},
        conversation=[{"role": "USER", "content": "api_key=sk-secret-value", "message_type": "TEXT"}],
    )
    assert "sk-secret-value" not in export["markdown"]
    assert "***" in export["markdown"]
    assert export["manifest"]["digest"]


def test_quarantine_payload() -> None:
    meta = quarantine_payload({}, kind="transcript", reason="corrupt json", ref="sid")
    assert meta["quarantine_active"] is True
    assert meta["regent_quarantine"][0]["kind"] == "transcript"
