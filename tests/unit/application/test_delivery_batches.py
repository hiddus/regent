"""Tests for incremental delivery batches (Phases A–D)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from regent.agent.generator import _materialize_incremental_changes
from regent.agent.subagent import SubagentBrief, SubagentRunner
from regent.agent.types import (
    AgentBudget,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    ToolCall,
)
from regent.application.delivery_batch_pipeline import _diff_to_changeset, _seed_sandbox
from regent.application.delivery_batch_service import (
    BATCH_MERGED,
    propose_delivery_batches,
    transition_batch,
)
from regent.application.p1_contracts import FileOperation
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.code_generator import ArtifactUriResolver
from regent.infrastructure.models import DeliveryBatchModel
from regent.infrastructure.workspace_writer import WorkspaceWriter


def test_propose_batches_splits_layers() -> None:
    specs = propose_delivery_batches(
        [
            "requirements.txt",
            "README.md",
            "src/app.py",
            "index.html",
            "static/app.css",
        ],
        force_incremental=True,
    )
    assert len(specs) >= 2
    assert specs[0].scope_paths
    assert specs[-1].is_final is True
    assert all(not s.is_final for s in specs[:-1])
    keys = [s.key for s in specs]
    assert any("scaffold" in k for k in keys)
    assert any("backend" in k or "frontend" in k for k in keys)


def test_propose_batches_force_slice_when_flat() -> None:
    specs = propose_delivery_batches(
        ["a.txt", "b.txt", "c.txt", "d.txt"],
        force_incremental=True,
    )
    assert len(specs) == 2
    assert set(specs[0].scope_paths) | set(specs[1].scope_paths) == {
        "a.txt",
        "b.txt",
        "c.txt",
        "d.txt",
    }


def test_batch_transition_state_machine() -> None:
    row = DeliveryBatchModel(
        id=uuid.uuid4(),
        goal_id=uuid.uuid4(),
        app_project_id=uuid.uuid4(),
        batch_ordinal=1,
        batch_key="scaffold",
        title="scaffold",
        status="PLANNED",
        version=0,
        attempt=1,
        is_final=False,
        scope_paths=[],
        acceptance_json={},
        verification_json={},
        summary_json={},
        correlation_id="c",
        metadata_json={},
        milestone_key="",
    )
    transition_batch(row, "GENERATING")
    transition_batch(row, "VERIFYING")
    transition_batch(row, "MERGED")
    assert row.status == BATCH_MERGED
    with pytest.raises(ValueError):
        transition_batch(row, "GENERATING")


def test_incremental_materialize_create_replace(tmp_path: Path) -> None:
    artifacts = FileArtifactStore(tmp_path / "arts")
    scope = uuid.uuid4()
    base = {"src/app.py": b"old\n"}
    files = {"src/app.py": "new\n", "README.md": "hi\n"}
    changes = _materialize_incremental_changes(
        base_files=base,
        files=files,
        planned={"src/app.py", "README.md"},
        artifacts=artifacts,
        scope=scope,
    )
    ops = {c.relative_path: c.operation for c in changes}
    assert ops["src/app.py"] is FileOperation.REPLACE
    assert ops["README.md"] is FileOperation.CREATE
    replace = next(c for c in changes if c.operation is FileOperation.REPLACE)
    assert replace.expected_previous_hash is not None


def test_diff_to_changeset_and_writer_merge(tmp_path: Path) -> None:
    arts_root = tmp_path / "arts"
    artifacts = FileArtifactStore(arts_root)
    writer = WorkspaceWriter(tmp_path / "ws", ArtifactUriResolver(arts_root))
    scope = uuid.uuid4()

    first = _diff_to_changeset(
        base_dir=None,
        files={"requirements.txt": "flask\n", "README.md": "v1\n"},
        artifacts=artifacts,
        scope=scope,
        generator_ref="agentic-batch-v1",
        prompt_version="v1",
    )
    assert all(c.operation is FileOperation.CREATE for c in first.changes)
    base_commit = writer.apply("base", first)

    second = _diff_to_changeset(
        base_dir=base_commit.workspace_path,
        files={
            "requirements.txt": "flask\n",
            "README.md": "v2\n",
            "src/app.py": "app=1\n",
        },
        artifacts=artifacts,
        scope=scope,
        generator_ref="agentic-batch-v1",
        prompt_version="v1",
    )
    ops = {c.relative_path: c.operation for c in second.changes}
    assert ops["README.md"] is FileOperation.REPLACE
    assert ops["src/app.py"] is FileOperation.CREATE
    assert "requirements.txt" not in ops
    next_commit = writer.apply(
        "next", second, base_workspace=base_commit.workspace_path
    )
    assert (next_commit.workspace_path / "README.md").read_text(encoding="utf-8") == "v2\n"
    assert (next_commit.workspace_path / "src/app.py").read_text(encoding="utf-8") == "app=1\n"
    assert (next_commit.workspace_path / "requirements.txt").read_text(encoding="utf-8") == "flask\n"


def test_seed_sandbox_copies_base(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "a.py").write_text("x", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    _seed_sandbox(sandbox, base)
    assert (sandbox / "a.py").read_text(encoding="utf-8") == "x"


def test_batch_acceptance_smoke_only_on_final() -> None:
    specs = propose_delivery_batches(
        ["requirements.txt", "README.md", "src/app.py", "index.html"],
        milestone_key="m1",
        milestone_title="MVP",
    )
    assert len(specs) >= 2
    assert specs[0].acceptance.get("batch_run_smoke") is False
    assert specs[-1].acceptance.get("batch_run_smoke") is True


class _ScriptedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools=None, temperature: float = 0):  # noqa: ANN001
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
                            arguments={
                                "path": "src/app.py",
                                "content": "from flask import Flask\napp=Flask(__name__)\n",
                            },
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
                content="submit",
                tool_calls=[
                    ToolCall(
                        id="2",
                        name="submit",
                        arguments={"summary": "milestone backend ready"},
                    )
                ],
            ),
            usage=ChatUsage(1, 1),
            model="fake",
            finish_reason="tool_calls",
        )


@pytest.mark.asyncio
async def test_subagent_seeded_incremental_files(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "requirements.txt").write_text("flask\n", encoding="utf-8")

    runner = SubagentRunner(
        _ScriptedProvider(),
        workspace_root=tmp_path / "batches",
        budget=AgentBudget(max_turns=5, max_tokens=50_000, max_wall_seconds=30),
    )
    sandbox = tmp_path / "batches" / "subagents" / "1-m1-backend"
    _seed_sandbox(sandbox, base)

    result = await runner.run_milestone(
        goal_anchor_text="build app",
        success_criteria={"usable": True},
        brief=SubagentBrief(
            milestone_key="m1-backend",
            milestone_title="backend",
            milestone_ordinal=1,
            acceptance={"batch_run_smoke": False},
            planned_paths=["src/app.py", "requirements.txt"],
        ),
        verify=False,
    )
    assert "src/app.py" in result.files
    assert (sandbox / "requirements.txt").exists() or "requirements.txt" in result.files
