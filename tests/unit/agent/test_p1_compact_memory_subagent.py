"""Tests for P1 compact / memory / subagent and P2 hygiene."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from regent.application.memory_service import AdmitMemory, MemoryKind, MemoryService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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


def test_micro_compact_drops_stale_raw_reasoning() -> None:
    messages = [
        ChatMessage(role="assistant", content=f"decision {i}", reasoning_content=f"raw {i}")
        for i in range(4)
    ]
    compacted = micro_compact(messages)
    assert compacted[0].reasoning_content is None
    assert compacted[1].reasoning_content is None
    assert compacted[-1].reasoning_content == "raw 3"


def test_micro_compact_strips_old_write_file_bodies() -> None:
    messages: list[ChatMessage] = []
    for i in range(10):
        body = ("line\n" * 40) + f"file-{i}"
        messages.append(
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"c{i}",
                        name="write_file",
                        arguments={"path": f"src/f{i}.py", "content": body},
                    )
                ],
            )
        )
        messages.append(
            ChatMessage(
                role="tool",
                content="ok",
                tool_call_id=f"c{i}",
                name="write_file",
            )
        )
    compacted = micro_compact(messages, keep_recent=3)
    write_assistants = [
        m
        for m in compacted
        if m.role == "assistant" and m.tool_calls and m.tool_calls[0].name == "write_file"
    ]
    # Older writes stripped; recent 3 keep full content.
    for msg in write_assistants[:-3]:
        content = msg.tool_calls[0].arguments.get("content", "")
        assert "[cleared" in content
        assert "file-" not in content or "re-read" in content
    for msg in write_assistants[-3:]:
        assert "file-" in msg.tool_calls[0].arguments["content"]


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


def test_successful_verification_removes_resolved_gap_memory(tmp_path: Path) -> None:
    svc = ProjectMemoryService(projects_root=tmp_path)
    old = svc.distill_regent_md(
        existing="",
        goal_text="build",
        stack_hints=[],
        structure=[],
        gaps=["SMOKE_FAILED: old failure"],
        verification_summary="failed",
        verification_passed=False,
    )
    current = svc.distill_regent_md(
        existing=old,
        goal_text="build",
        stack_hints=[],
        structure=[],
        gaps=[],
        verification_summary="passed",
        verification_passed=True,
    )
    assert "old failure" not in current


@pytest.mark.asyncio
async def test_relevant_memory_uses_verified_semantic_rows_only() -> None:
    svc = ProjectMemoryService()
    memories = AsyncMock()
    memories.query_by_kind.return_value = [
        SimpleNamespace(
            id=uuid.uuid4(),
            created_at=datetime.now(UTC),
            content_json={"pattern": "verified_delivery_stack", "stack": ["sqlite"]},
        )
    ]
    svc._memories = memories  # type: ignore[attr-defined]  # noqa: SLF001

    result = await svc.relevant_verified_memory(
        "org", query="build a sqlite application"
    )

    assert "verified_memory=" in result
    assert "sqlite" in result
    assert memories.query_by_kind.await_args.kwargs["verified_only"] is True


@pytest.mark.asyncio
async def test_project_hard_rule_can_be_recalled_without_keyword_overlap() -> None:
    svc = ProjectMemoryService()
    memories = AsyncMock()
    memories.query_by_kind.return_value = [
        SimpleNamespace(
            id=uuid.uuid4(),
            created_at=datetime.now(UTC),
            content_json={"rule": "不要使用 GraphQL", "authority": "explicit_user_instruction"},
        )
    ]
    svc._memories = memories  # type: ignore[attr-defined]  # noqa: SLF001
    result = await svc.relevant_verified_memory(
        "project:p",
        query="build an admin dashboard",
        include_unmatched=True,
    )
    assert "不要使用 GraphQL" in result


@pytest.mark.asyncio
async def test_semantic_pattern_requires_two_distinct_goal_observations(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = MemoryService(db_sessions)
    first_goal = uuid.uuid4()
    second_goal = uuid.uuid4()

    first = await service.reinforce_semantic(
        AdmitMemory(
            org_key="org",
            kind=MemoryKind.SEMANTIC_PATTERN.value,
            content={"pattern": "sqlite-stack"},
            actor="test",
            goal_id=first_goal,
        ),
        memory_key="sqlite-stack",
    )
    again_same_goal = await service.reinforce_semantic(
        AdmitMemory(
            org_key="org",
            kind=MemoryKind.SEMANTIC_PATTERN.value,
            content={"pattern": "sqlite-stack"},
            actor="test",
            goal_id=first_goal,
        ),
        memory_key="sqlite-stack",
    )
    verified = await service.reinforce_semantic(
        AdmitMemory(
            org_key="org",
            kind=MemoryKind.SEMANTIC_PATTERN.value,
            content={"pattern": "sqlite-stack"},
            actor="test",
            goal_id=second_goal,
        ),
        memory_key="sqlite-stack",
    )

    assert first.status == "CANDIDATE"
    assert again_same_goal.status == "CANDIDATE"
    assert verified.status == "VERIFIED"
    assert verified.content_json["_observation_count"] == 2


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


@pytest.mark.asyncio
async def test_subagent_inherits_hard_budget_ledger(tmp_path: Path) -> None:
    from types import SimpleNamespace

    class Ledger:
        def __init__(self) -> None:
            self.reservations = 0
            self.settlements = 0

        async def reserve(self, goal_id, run_id, **kwargs):  # noqa: ANN001
            self.reservations += 1
            return SimpleNamespace(id=uuid.uuid4(), claim_token=None)

        async def claim(self, reservation_id):  # noqa: ANN001
            return SimpleNamespace(id=reservation_id, claim_token=uuid.uuid4())

        async def settle(self, reservation_id, **kwargs):  # noqa: ANN001
            self.settlements += 1

        async def release(self, reservation_id, **kwargs):  # noqa: ANN001
            raise AssertionError("successful subagent call must settle, not release")

    ledger = Ledger()
    runner = SubagentRunner(
        _Scripted(),
        workspace_root=tmp_path,
        budget=AgentBudget(max_turns=5, max_tokens=10_000, max_wall_seconds=30),
        goal_id=str(uuid.uuid4()),
        run_id=uuid.uuid4(),
        budget_ledger=ledger,
        model_max_output_tokens=10,
        model_input_cost_per_million=1.0,
        model_output_cost_per_million=1.0,
    )
    await runner.run_milestone(
        goal_anchor_text="build community",
        success_criteria={"has_api": True},
        brief=SubagentBrief(
            milestone_key="budgeted", milestone_title="Budgeted child",
            milestone_ordinal=2, planned_paths=["README.md"],
        ),
        verify=False,
    )
    assert ledger.reservations == 2
    assert ledger.settlements == 2


def test_estimate_tokens_positive() -> None:
    assert estimate_tokens([ChatMessage(role="user", content="abcd" * 10)]) >= 1
