"""A0 Agent Loop exit contract unit tests."""

from __future__ import annotations

from regent.application.agent_loop_exit import (
    apply_exit_to_metadata,
    build_ask_envelope,
    build_exit,
    build_result_bundle,
    conversation_copy_for_exit,
    detect_doom_loop,
    has_unanswered_ask,
    mark_ask_answered,
)


def test_build_complete_exit_bundle() -> None:
    bundle = build_result_bundle(
        summary="done",
        preview_url="http://x/preview",
        open_items=[],
        change_points=["edit a"],
    )
    exit_row = build_exit(
        exit_kind="COMPLETE",
        stop_reason="verified_pass",
        lease_id="run-1",
        result_bundle=bundle,
    )
    assert exit_row["exit_kind"] == "COMPLETE"
    assert exit_row["result_bundle"]["preview_url"]
    msg_type, content = conversation_copy_for_exit(exit_row)
    assert msg_type == "AGENT_LOOP_COMPLETE"
    assert "完成" in content


def test_ask_and_answer_cycle() -> None:
    ask = build_ask_envelope(
        question="怎么继续？",
        why_blocked="验证失败",
        gap_kind="TEST_FAILED",
    )
    exit_row = build_exit(
        exit_kind="ASK_HUMAN",
        stop_reason="verification_gap",
        ask_envelope=ask,
    )
    meta = apply_exit_to_metadata({}, exit_row)
    assert has_unanswered_ask(meta)
    meta2 = mark_ask_answered(meta, answer="缩小范围", option_id="continue_fix")
    assert not has_unanswered_ask(meta2)
    assert meta2["pending_agent_loop_ask"]["answered"] is True


def test_detect_doom_loop_same_gap() -> None:
    meta = {
        "delivery_gap_kind": "presentation",
        "delivery_gap_kind_streak": 1,
        "session_resume_attempts": 1,
    }
    is_doom, reason = detect_doom_loop(meta, gap_kind="presentation")
    assert is_doom is True
    assert "same_gap" in reason


def test_stop_conversation_copy() -> None:
    exit_row = build_exit(exit_kind="STOP", stop_reason="budget", draft_uri="file://x")
    msg_type, content = conversation_copy_for_exit(exit_row)
    assert msg_type == "AGENT_LOOP_STOP"
    assert "停止" in content
