"""Tests for P1 compact / memory / subagent and P2 hygiene."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from regent.agent.compact import (
    ContextCompactor,
    HeuristicSummarizer,
    estimate_tokens,
    micro_compact,
)
from regent.agent.project_memory import ProjectMemoryService, _clip_regent_md
from regent.agent.subagent import SubagentBrief, SubagentRunner
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import AgentBudget, ChatMessage, ChatResponse, ChatUsage, ToolCall
from regent.agent.types import BudgetExhaustedError


def test_micro_compact_clears_old_tool_results() -> None:
    messages = [
        ChatMessage(role="user", content="hi"),
    ]
    for i in range(12):
        messages.append(
            ChatMessage(
                role="tool",
                content=f"result-{i}" * 20,
                tool_call_id=str(i),
                name="run_command",
            )
        )
    compacted = micro_compact(messages, keep_recent=8)
    tool_msgs = [m for m in compacted if m.role == "tool"]
    assert sum(1 for m in tool_msgs if m.content == "[cleared]") == 4
    assert all(m.content != "[cleared]" for m in tool_msgs[-8:])


@pytest.mark.asyncio
async def test_auto_compact_rehydrates(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)
    toolkit.write_text("src/app.py", "app = object()\n")
    toolkit.todos = [{"id": "1", "content": "build api", "status": "in_progress"}]
    compactor = ContextCompactor(
        toolkit=toolkit,
        summarizer=HeuristicSummarizer(),
        context_window_tokens=200,
        buffer_tokens=50,
    )
    huge = [ChatMessage(role="user", content="x" * 2000) for _ in range(5)]
    assert compactor.needs_auto_compact(huge)
    result = await compactor.maybe_auto_compact(
        huge,
        goal_anchor="GOAL: skills network",
        todos=toolkit.todos,
    )
    assert result.did_compact
    blob = "\n".join(m.content or "" for m in result.messages)
    assert "POST-COMPACT" in blob
    assert "skills network" in blob
    assert "src/app.py" in blob


@pytest.mark.asyncio
async def test_auto_compact_circuit_breaker(tmp_path: Path) -> None:
    class Boom:
        async def summarize(self, text: str) -> str:
            raise RuntimeError("boom")

    toolkit = WorkspaceToolkit(tmp_path)
    compactor = ContextCompactor(
        toolkit=toolkit,
        summarizer=Boom(),  # type: ignore[arg-type]
        context_window_tokens=100,
        buffer_tokens=20,
    )
    msgs = [ChatMessage(role="user", content="y" * 800)]
    for _ in range(2):
        out = await compactor.maybe_auto_compact(msgs, goal_anchor="g", todos=[])
        assert out.failed
    with pytest.raises(BudgetExhaustedError):
        await compactor.maybe_auto_compact(msgs, goal_anchor="g", todos=[])


def test_regent_md_clip_and_distill(tmp_path: Path) -> None:
    svc = ProjectMemoryService(projects_root=tmp_path)
    project_id = uuid.uuid4()
    text = svc.distill_regent_md(
        existing="",
        goal_text="AI skills network",
        stack_hints=["flask", "sqlite"],
        structure=["src/app.py", "index.html"],
        gaps=["forbid-pure-static-backend: x"],
        verification_summary="PASS",
    )
    path = svc.write_regent_md(project_id, text)
    assert path is not None
    loaded = svc.load_regent_md(project_id)
    assert "AI skills network" in loaded
    assert "flask" in loaded
    huge = "line\n" * 500
    clipped = _clip_regent_md(huge)
    assert clipped.count("\n") <= 201


class _Scripted:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools=None, temperature: float = 0):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="write",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={"path": "README.md", "content": "milestone\n"},
                        )
                    ],
                ),
                usage=ChatUsage(1, 1),
                model="fake",
                finish_reason="tool_calls",
            )
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="done",
                tool_calls=[
                    ToolCall(id="s", name="submit", arguments={"summary": "milestone"}),
                ],
            ),
            usage=ChatUsage(1, 1),
            model="fake",
            finish_reason="tool_calls",
        )


@pytest.mark.asyncio
async def test_subagent_returns_summary_without_sidechain(tmp_path: Path) -> None:
    runner = SubagentRunner(
        _Scripted(),
        workspace_root=tmp_path,
        budget=AgentBudget(max_turns=5, max_tokens=10_000, max_wall_seconds=30),
        regent_md="# prior memory\n",
    )
    result = await runner.run_milestone(
        goal_anchor_text="build community",
        success_criteria={"has_api": True},
        brief=SubagentBrief(
            milestone_key="m1",
            milestone_title="First slice",
            milestone_ordinal=1,
            planned_paths=["README.md"],
        ),
        verify=False,
    )
    assert result.summary["sidechain_omitted"] is True
    assert "conversation" not in result.summary
    assert "README.md" in result.files


def test_estimate_tokens_positive() -> None:
    assert estimate_tokens([ChatMessage(role="user", content="abcd" * 10)]) >= 1
