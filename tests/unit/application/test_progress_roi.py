"""Progress ROI gate unit tests."""

from __future__ import annotations

from pathlib import Path

from regent.application.agent_loop_exit import detect_doom_loop
from regent.application.progress_roi import (
    META_PROGRESS_ROI,
    apply_roi_on_exit,
    authorize_resume_by_roi,
    build_progress_snapshot,
    compute_workspace_hash,
    evaluate_cycle_roi,
    human_message_is_substantive,
    next_action_for_streak,
    stamp_cycle_start,
)


def test_evaluate_stagnant_same_gaps_with_tokens() -> None:
    before = {
        "gap_kind": "product_surface",
        "gap_reasons": ["PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline"],
        "gap_set_hash": "abc",
        "blocking_gaps": ["delivery-product-outline"],
        "workspace_hash": "ws1",
        "session_resume_attempts": 1,
        "tokens_spent": 0,
        "preview_ready": False,
        "product_surface_ready": False,
        "qa_failure_count": 2,
        "swarm_gap_count": 2,
    }
    after = {
        **before,
        "gap_set_hash": "abc",
        "tokens_spent": 5000,
        "session_resume_attempts": 2,
    }
    # Recompute hashes via snapshot helpers for realism
    before_s = build_progress_snapshot(
        {"delivery_gap_kind": "product_surface"},
        workspace_hash="ws1",
        ledger={"input_tokens": 0, "output_tokens": 0},
        gap_reasons=["PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline"],
        gap_kind="product_surface",
    )
    after_s = build_progress_snapshot(
        {
            "delivery_gap_kind": "product_surface",
            "session_resume_attempts": 2,
        },
        workspace_hash="ws1",
        ledger={"input_tokens": 4000, "output_tokens": 1000},
        gap_reasons=["PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline"],
        gap_kind="product_surface",
    )
    result = evaluate_cycle_roi(before_s, after_s, min_tokens=2000)
    assert result["verdict"] == "stagnant"
    assert result["had_spend"] is True
    assert result["progressed"] is False


def test_evaluate_progressed_when_gaps_shrink() -> None:
    before = build_progress_snapshot(
        {},
        workspace_hash="ws1",
        ledger={},
        gap_reasons=[
            "PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline",
            "PREVIEW_PRODUCT_QA_FAILED: delivery-ux-surface",
        ],
        gap_kind="product_surface",
    )
    after = build_progress_snapshot(
        {"session_resume_attempts": 2},
        workspace_hash="ws2",
        ledger={"input_tokens": 3000, "output_tokens": 500},
        gap_reasons=["PREVIEW_PRODUCT_QA_FAILED: delivery-ux-surface"],
        gap_kind="product_surface",
    )
    result = evaluate_cycle_roi(before, after, min_tokens=2000)
    assert result["verdict"] == "progressed"
    assert result["progressed"] is True


def test_apply_roi_ladder_self_repair_then_replan_then_stop() -> None:
    meta: dict = {
        "delivery_gap_kind": "product_surface",
        "session_resume_attempts": 1,
    }
    snap1 = build_progress_snapshot(
        meta,
        workspace_hash="ws1",
        ledger={"input_tokens": 5000, "output_tokens": 0},
        gap_reasons=["PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline"],
        gap_kind="product_surface",
    )
    meta, roi = apply_roi_on_exit(meta, snapshot=snap1, min_tokens=2000, stagnant_stop=3)
    # First exit without cycle_start → baseline
    assert roi["verdict"] == "baseline"
    assert roi["stagnant_streak"] == 0
    meta = stamp_cycle_start(meta, snap1)

    snap2 = build_progress_snapshot(
        {**meta, "session_resume_attempts": 2},
        workspace_hash="ws1",
        ledger={"input_tokens": 5000, "output_tokens": 0},
        gap_reasons=["PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline"],
        gap_kind="product_surface",
    )
    meta, roi = apply_roi_on_exit(meta, snapshot=snap2, min_tokens=2000, stagnant_stop=3)
    assert roi["verdict"] == "stagnant"
    assert roi["stagnant_streak"] == 1
    assert roi["next_action"] == "self_repair"
    meta = stamp_cycle_start(meta, snap2)

    snap3 = build_progress_snapshot(
        {**meta, "session_resume_attempts": 3},
        workspace_hash="ws1",
        ledger={"input_tokens": 5000, "output_tokens": 0},
        gap_reasons=["PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline"],
        gap_kind="product_surface",
    )
    meta, roi = apply_roi_on_exit(meta, snapshot=snap3, min_tokens=2000, stagnant_stop=3)
    assert roi["stagnant_streak"] == 2
    assert roi["next_action"] == "replan_global"
    meta = stamp_cycle_start(meta, snap3)

    snap4 = build_progress_snapshot(
        {**meta, "session_resume_attempts": 4},
        workspace_hash="ws1",
        ledger={"input_tokens": 5000, "output_tokens": 0},
        gap_reasons=["PREVIEW_PRODUCT_QA_FAILED: delivery-product-outline"],
        gap_kind="product_surface",
    )
    meta, roi = apply_roi_on_exit(meta, snapshot=snap4, min_tokens=2000, stagnant_stop=3)
    assert roi["stagnant_streak"] == 3
    assert roi["next_action"] == "stop"


def test_authorize_rewrites_empty_continue_fix() -> None:
    meta = {
        META_PROGRESS_ROI: {
            "stagnant_streak": 1,
            "next_action": "self_repair",
            "repair_constraints": ["MUST thicken homepage"],
            "summary": "no progress",
        }
    }
    auth = authorize_resume_by_roi(
        meta, option_id="continue_fix", human_message="继续", enforced=True
    )
    assert auth["allowed"] is True
    assert auth["option_id"] == "self_repair"
    assert any("thicken" in c.lower() or "MUST" in c for c in auth["inject_constraints"])


def test_authorize_stop_without_substance() -> None:
    meta = {
        META_PROGRESS_ROI: {
            "stagnant_streak": 3,
            "next_action": "stop",
            "repair_constraints": [],
            "summary": "stop",
        }
    }
    auth = authorize_resume_by_roi(
        meta, option_id="continue_fix", human_message="continue_fix", enforced=True
    )
    assert auth["allowed"] is False
    assert auth["force_stop"] is True


def test_authorize_substantive_after_stop_allows_replan() -> None:
    meta = {
        META_PROGRESS_ROI: {
            "stagnant_streak": 3,
            "next_action": "stop",
            "repair_constraints": ["x"],
            "summary": "stop",
        }
    }
    msg = (
        "replan_global：根因是首页可见字不足，必须改 templates/index.html "
        "加厚产品说明与示例四段，禁止只改详情页。"
    )
    assert human_message_is_substantive(msg)
    auth = authorize_resume_by_roi(meta, option_id="continue_fix", human_message=msg)
    assert auth["allowed"] is True
    assert auth["option_id"] == "replan_global"
    assert auth["work_plan_replan"] is True
    assert auth["reset_streak"] is True


def test_next_action_for_streak() -> None:
    assert next_action_for_streak(0) == "continue_fix"
    assert next_action_for_streak(1) == "self_repair"
    assert next_action_for_streak(2) == "replan_global"
    assert next_action_for_streak(3) == "stop"


def test_compute_workspace_hash_changes_with_file(tmp_path: Path) -> None:
    (tmp_path / "templates").mkdir()
    f = tmp_path / "templates" / "index.html"
    f.write_text("<h1>a</h1>", encoding="utf-8")
    h1 = compute_workspace_hash(tmp_path)
    f.write_text("<h1>ab thicker homepage content here</h1>", encoding="utf-8")
    h2 = compute_workspace_hash(tmp_path)
    assert h1 and h2 and h1 != h2


def test_detect_doom_loop_roi_streak(monkeypatch) -> None:
    class _S:
        progress_roi_enforced = True
        progress_roi_stagnant_stop = 3

    monkeypatch.setattr(
        "regent.config.get_settings", lambda: _S()
    )
    meta = {
        META_PROGRESS_ROI: {"stagnant_streak": 3},
        "delivery_gap_kind": "product_surface",
        "delivery_gap_kind_streak": 0,
        "session_resume_attempts": 1,
    }
    is_doom, reason = detect_doom_loop(meta, gap_kind="product_surface")
    assert is_doom is True
    assert "roi_no_progress" in reason


def test_detect_doom_loop_workspace_unchanged() -> None:
    meta = {
        "agent_loop_workspace_hash": "samehash",
        "delivery_gap_kind": "product_surface",
        "delivery_gap_kind_streak": 0,
        "session_resume_attempts": 2,
        META_PROGRESS_ROI: {"stagnant_streak": 0},
    }
    is_doom, reason = detect_doom_loop(
        meta, gap_kind="navigation", workspace_hash="samehash"
    )
    assert is_doom is True
    assert reason == "doom_loop:workspace_unchanged"
