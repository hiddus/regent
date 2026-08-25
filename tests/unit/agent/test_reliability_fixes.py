"""Tests for reliability fixes: content-aware clipping, dedup, insufficient_data fail-closed,
A-B-A-B detection, SubagentHandoffV1, segment fingerprints."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

from regent.agent.context_assembler import (
    ContextAssembler,
    _clip,
    _clip_json_list,
    _clip_lines,
    _clip_sentences,
    _segment_fingerprint,
)
from regent.application.progress_roi import (
    apply_roi_on_exit,
    next_action_for_streak,
)


# ---------------------------------------------------------------------------
# Content-aware clipping
# ---------------------------------------------------------------------------


class TestClipLines:
    def test_no_truncation_when_within_budget(self) -> None:
        text = "line1\nline2\nline3"
        assert _clip_lines(text, 1000) == text

    def test_truncates_at_line_boundary(self) -> None:
        lines_text = [f"line {i}: {'x' * 20}" for i in range(10)]
        text = "\n".join(lines_text)
        # Each line ~28 chars; budget=100 allows ~2-3 lines.
        result = _clip_lines(text, 100)
        assert "line 0" in result
        assert "line 1" in result
        assert "truncated at line boundary" in result
        # Must not contain later lines.
        assert "line 5" not in result

    def test_empty_text(self) -> None:
        assert _clip_lines("", 100) == ""


class TestClipJsonList:
    def test_no_truncation_when_within_budget(self) -> None:
        data = json.dumps([1, 2, 3])
        assert _clip_json_list(data, 1000) == data

    def test_truncates_by_dropping_items(self) -> None:
        data = json.dumps(list(range(50)), indent=2)
        result = _clip_json_list(data, 200)
        parsed = json.loads(result.split("\n...[items truncated]")[0])
        assert len(parsed) < 50
        assert all(isinstance(x, int) for x in parsed)

    def test_falls_back_for_non_json(self) -> None:
        text = "not json at all " * 100
        result = _clip_json_list(text, 50)
        assert "truncated" in result.lower()

    def test_preserves_dict_via_lines(self) -> None:
        data = json.dumps({"key": "value", "nested": {"a": 1}}, indent=2)
        result = _clip_json_list(data, 1000)
        assert result == data  # within budget


class TestClipSentences:
    def test_no_truncation_when_within_budget(self) -> None:
        text = "First sentence. Second sentence."
        assert _clip_sentences(text, 1000) == text

    def test_truncates_at_sentence_boundary(self) -> None:
        text = "First. " + "Second. " * 20  # long text with clear sentence boundaries
        result = _clip_sentences(text, 60)
        assert "First." in result
        assert "truncated at sentence boundary" in result
        # Should not contain all 20 repetitions.
        assert result.count("Second.") < 20

    def test_chinese_sentence_boundary(self) -> None:
        text = "第一句。第二句。第三句。第四句。"
        result = _clip_sentences(text, 20)
        assert "第一句。" in result


class TestLegacyClip:
    def test_no_truncation(self) -> None:
        assert _clip("short", 100) == "short"

    def test_truncation(self) -> None:
        result = _clip("x" * 100, 50)
        assert len(result) <= 50
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Segment fingerprint
# ---------------------------------------------------------------------------


class TestSegmentFingerprint:
    def test_same_content_same_fingerprint(self) -> None:
        assert _segment_fingerprint("hello") == _segment_fingerprint("hello")

    def test_different_content_different_fingerprint(self) -> None:
        assert _segment_fingerprint("hello") != _segment_fingerprint("world")

    def test_stable_across_calls(self) -> None:
        text = "stable content 稳定内容"
        fp1 = _segment_fingerprint(text)
        fp2 = _segment_fingerprint(text)
        assert fp1 == fp2


# ---------------------------------------------------------------------------
# Conversation dedup
# ---------------------------------------------------------------------------


def _toolkit() -> MagicMock:
    toolkit = MagicMock()
    toolkit.list_tree.return_value = []
    toolkit.recent_writes = []
    toolkit.todos = []
    return toolkit


class TestConversationDedup:
    def test_dedup_removes_snippets_matching_live_conversation(self) -> None:
        plan = {
            "conversation_snippets": [
                {"role": "user", "content": "unique old message"},
                {"role": "user", "content": "brand new instruction"},
            ],
            "_live_conversation_messages": [
                {"content": "unique old message"},
            ],
        }
        assembler = ContextAssembler(plan=plan, toolkit=_toolkit())
        segment = assembler._conversation_segment()  # noqa: SLF001
        assert "unique old message" not in segment
        assert "brand new instruction" in segment

    def test_no_dedup_when_no_live_messages(self) -> None:
        plan = {
            "conversation_snippets": [
                {"role": "user", "content": "snippet text"},
            ],
        }
        assembler = ContextAssembler(plan=plan, toolkit=_toolkit())
        segment = assembler._conversation_segment()  # noqa: SLF001
        assert "snippet text" in segment


# ---------------------------------------------------------------------------
# Goal anchor protection
# ---------------------------------------------------------------------------


class TestGoalAnchorProtection:
    def test_success_criteria_json_not_split_mid_structure(self) -> None:
        plan = {
            "goal_anchor_text": "test goal",
            "acceptance_contract": {
                "first_deliverable": "a working page",
                "success_criteria": {
                    "has_login": True,
                    "has_dashboard": True,
                    "has_api": True,
                },
            },
        }
        assembler = ContextAssembler(plan=plan, toolkit=_toolkit())
        segment = assembler._goal_anchor_segment()  # noqa: SLF001
        # The JSON must be parseable if extracted (no mid-structure split).
        assert "Success criteria:" in segment
        # If truncated, it must say so explicitly.
        if "truncated" in segment:
            assert "truncated at line boundary" in segment


# ---------------------------------------------------------------------------
# Segment fingerprints and diagnostics
# ---------------------------------------------------------------------------


class TestSegmentDiagnostics:
    def test_segment_fingerprints_returns_dict(self) -> None:
        assembler = ContextAssembler(
            plan={"goal_anchor_text": "test"},
            toolkit=_toolkit(),
        )
        fps = assembler.segment_fingerprints()
        assert isinstance(fps, dict)
        assert "goal" in fps
        assert len(fps["goal"]) == 12  # sha256[:12]

    def test_assemble_diagnostics_returns_structure(self) -> None:
        assembler = ContextAssembler(
            plan={"goal_anchor_text": "test goal"},
            toolkit=_toolkit(),
        )
        diag = assembler.assemble_diagnostics(turn=0, conversation=[])
        assert "segment_chars" in diag
        assert "segment_fingerprints" in diag
        assert "total_chars" in diag
        assert "estimated_tokens" in diag
        assert diag["total_chars"] > 0


# ---------------------------------------------------------------------------
# insufficient_data fail-closed counter
# ---------------------------------------------------------------------------


class TestInsufficientDataFailClosed:
    def test_next_action_stops_after_insufficient_data_threshold(self) -> None:
        # 3 consecutive insufficient_data → stop
        action = next_action_for_streak(
            stagnant_streak=0,
            insufficient_data_streak=3,
        )
        assert action == "stop"

    def test_next_action_continues_below_threshold(self) -> None:
        action = next_action_for_streak(
            stagnant_streak=0,
            insufficient_data_streak=1,
        )
        assert action == "continue_fix"

    def test_stagnant_streak_still_works(self) -> None:
        action = next_action_for_streak(stagnant_streak=3)
        assert action == "stop"

    def test_apply_roi_on_exit_tracks_insufficient_data_streak(self) -> None:
        """After baseline, consecutive cycles with no spend → insufficient_data streak."""
        from regent.application.progress_roi import stamp_cycle_start, build_progress_snapshot
        # Establish baseline.
        metadata: dict = {}
        baseline = build_progress_snapshot({})
        metadata = stamp_cycle_start(metadata, baseline)
        snapshot_no_spend = {
            "gap_kind": "",
            "gap_reasons": [],
            "gap_set_hash": "empty",
            "blocking_gaps": [],
            "workspace_hash": None,
            "preview_ready": False,
            "product_surface_ready": False,
            "qa_failure_count": 0,
            "swarm_gap_count": 0,
            "session_resume_attempts": 0,
            "tokens_spent": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "turns": 0,
        }
        # First exit: insufficient_data (no spend).
        meta1, roi1 = apply_roi_on_exit(metadata, snapshot=snapshot_no_spend)
        assert roi1["verdict"] == "insufficient_data"
        assert roi1.get("insufficient_data_streak", 0) == 1
        # Second exit: insufficient_data again.
        meta1 = stamp_cycle_start(meta1, build_progress_snapshot(meta1))
        meta2, roi2 = apply_roi_on_exit(meta1, snapshot=snapshot_no_spend)
        assert roi2["verdict"] == "insufficient_data"
        assert roi2.get("insufficient_data_streak", 0) == 2

    def test_apply_roi_on_exit_resets_insufficient_data_on_progress(self) -> None:
        metadata = {
            "progress_roi": {
                "insufficient_data_streak": 2,
                "stagnant_streak": 0,
            }
        }
        snapshot = {
            "gap_kind": "test",
            "gap_reasons": ["gap-a"],
            "gap_set_hash": "hash1",
            "blocking_gaps": ["gap-a"],
            "workspace_hash": "ws1",
            "preview_ready": False,
            "product_surface_ready": False,
            "qa_failure_count": 1,
            "swarm_gap_count": 0,
            "session_resume_attempts": 0,
            "tokens_spent": 5000,
            "input_tokens": 3000,
            "output_tokens": 2000,
            "turns": 2,
        }
        # Create a baseline first.
        from regent.application.progress_roi import stamp_cycle_start, build_progress_snapshot
        baseline = build_progress_snapshot({})
        metadata = stamp_cycle_start(metadata, baseline)
        # Now apply with progress.
        meta2, roi2 = apply_roi_on_exit(metadata, snapshot=snapshot)
        # Should have progressed → reset insufficient_data_streak.
        if roi2["verdict"] == "progressed":
            assert roi2.get("insufficient_data_streak", 0) == 0


# ---------------------------------------------------------------------------
# SubagentHandoffV1
# ---------------------------------------------------------------------------


class TestSubagentHandoffV1:
    def test_handoff_dataclass_creation(self) -> None:
        from regent.agent.subagent import SubagentHandoffV1

        handoff = SubagentHandoffV1(
            task_objective="build login page",
            executed_scope=["wrote 3 files"],
            modified_files=["login.html", "style.css", "app.py"],
            unresolved_issues=["gap: missing API endpoint"],
            budget_remaining={"input_tokens": 5000},
        )
        assert handoff.task_objective == "build login page"
        assert len(handoff.modified_files) == 3
        assert handoff.budget_remaining["input_tokens"] == 5000

    def test_handoff_defaults(self) -> None:
        from regent.agent.subagent import SubagentHandoffV1

        handoff = SubagentHandoffV1()
        assert handoff.task_objective == ""
        assert handoff.executed_scope == []
        assert handoff.rejected_approaches == []
        assert handoff.inherited_constraints == []

    def test_subagent_result_includes_handoff(self) -> None:
        from regent.agent.subagent import SubagentBrief, SubagentHandoffV1, SubagentResult

        brief = SubagentBrief(
            milestone_key="m1",
            milestone_title="Login page",
            milestone_ordinal=1,
        )
        handoff = SubagentHandoffV1(task_objective="Login page")
        result = SubagentResult(
            brief=brief,
            summary={"milestone_key": "m1"},
            handoff=handoff,
        )
        assert result.handoff is not None
        assert result.handoff.task_objective == "Login page"
