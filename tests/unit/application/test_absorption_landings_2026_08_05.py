"""Absorption plan landings: Ask bubble-up, result artifacts, zombie reclaim API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from regent.agent.agent_runner import _seed_session_conversation
from regent.agent.tools import ToolCall, WorkspaceToolkit
from regent.application.agent_control import AskUserRequiredError
from regent.application.agent_loop_exit import (
    build_ask_envelope,
    build_result_bundle,
    record_progress_attempt,
)
from regent.application.delivery_progress_watchdog import reclaim_generating_zombies
from regent.application.side_question import run_side_question


@pytest.mark.asyncio
async def test_ask_user_question_bubbles_out_of_toolkit(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path, command_sandbox=MagicMock())
    with pytest.raises(AskUserRequiredError) as ei:
        await toolkit.execute(
            ToolCall(
                id="1",
                name="ask_user_question",
                arguments={
                    "question": "用浅色还是深色主题？",
                    "options": [
                        {"id": "light", "label": "浅色"},
                        {"id": "dark", "label": "深色"},
                    ],
                },
            )
        )
    assert "浅色" in ei.value.question or "主题" in ei.value.question
    assert ei.value.envelope["ask_type"] == "ask_user"


def test_result_bundle_includes_artifacts() -> None:
    bundle = build_result_bundle(
        summary="done",
        preview_url="https://example.test/p",
        artifact_uri="artifact://primary",
        artifacts=[{"uri": "artifact://extra", "label": "日志", "kind": "log"}],
        open_items=["wire refresh"],
    )
    assert bundle["artifact_uri"] == "artifact://primary"
    assert len(bundle["artifacts"]) == 2
    assert bundle["artifacts"][0]["uri"] == "artifact://primary"
    assert bundle["open_items"] == ["wire refresh"]


def test_reclaim_generating_zombies_is_exportable() -> None:
    assert callable(reclaim_generating_zombies)


def test_progress_loop_ask_envelope_carries_blocked_item() -> None:
    meta: dict = {}
    for _ in range(3):
        meta, warning = record_progress_attempt(meta, item_key="wire-smoke", threshold=3)
    assert warning["loop_detected"] is True
    envelope = build_ask_envelope(
        question="stuck",
        why_blocked="no progress",
        ask_type="progress_loop",
        gap_kind="PROGRESS_LOOP",
        blocked_item_key="wire-smoke",
    )
    assert envelope["blocked_item_key"] == "wire-smoke"
    assert envelope["ask_type"] == "progress_loop"


def test_seed_includes_steering_and_failure_envelopes_as_user(tmp_path: Path) -> None:
    plan = {
        "active_corrections": [
            {"target": "ui", "detail": "use dark theme"},
            {"target": "api", "detail": "add POST /api/refresh"},
        ],
        "session_steer_brief": "make refresh work",
        "latest_goal_spec_version": 3,
        "failure_envelopes": [
            {
                "stage": "smoke",
                "summary": "GET / returned 404",
                "stderr": "No route for /",
            }
        ],
        "acceptance_contract": {},
    }
    seeded = _seed_session_conversation(plan, toolkit_root=tmp_path)
    texts = [m.content or "" for m in seeded if m.role == "user"]
    joined = "\n".join(texts)
    assert "Goal is evolving" in joined or "not a one-shot" in joined
    assert "dark theme" in joined
    assert "GoalSpec version: 3" in joined
    assert "Prior run failures" in joined
    assert "smoke" in joined


@pytest.mark.asyncio
async def test_side_question_uses_answerer_without_tools() -> None:
    seen: list[list] = []

    async def answerer(messages: list) -> str:
        seen.append(messages)
        return "side answer only"

    result = await run_side_question(
        question="what stack?",
        context_messages=[{"role": "user", "content": "build flask clock"}],
        answerer=answerer,
    )
    assert result["ok"] is True
    assert result["text"] == "side answer only"
    assert result["tools_invoked"] is False
    assert result["mutated_work_plan"] is False
    assert seen and any(
        "side question" in str(m.get("content") or "").lower() for m in seen[0]
    )
