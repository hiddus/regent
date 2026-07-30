"""Unit tests for agentic generation P0 pieces."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from regent.agent.agent_runner import AgentRunner
from regent.agent.context_assembler import ContextAssembler
from regent.agent.generator import AgenticCodeGenerator
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import (
    AgentBudget,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    ToolCall,
    VerificationGap,
)
from regent.agent.verification import VerificationAgent
from regent.application.delivery_review_service import review_files_for_delivery
from regent.infrastructure.artifact_store import FileArtifactStore


def test_review_rejects_pure_static_backend() -> None:
    files = {
        "index.html": "<!doctype html><html><head><title>x</title><style>body{max-width:40rem}</style>"
        "</head><body><main><h1>AI Skills</h1><button data-regent-event='activation'>Go</button>"
        "<ul><li>a</li><li>b</li></ul></main></body></html>",
        "requirements.txt": "flask\n",
        "src/app.py": (
            "from flask import Flask, send_from_directory\n"
            "app = Flask(__name__)\n"
            "@app.route('/')\n"
            "def index():\n"
            "    return send_from_directory('.', 'index.html')\n"
        ),
        "README.md": "demo\n",
    }
    result = review_files_for_delivery(files)
    assert not result.passed
    assert "forbid-pure-static-backend" in result.failed_gap_codes()


def test_context_assembler_includes_goal_and_failures(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)
    toolkit.write_text("src/app.py", "print('hi')\n")
    assembler = ContextAssembler(
        plan={
            "goal_anchor_text": "build a skills network for AI workers",
            "planned_paths": ["src/app.py", "index.html"],
            "acceptance_contract": {
                "first_deliverable": "skill feed",
                "success_criteria": {"users_can_connect": True},
                "delivery_gap_reasons": ["forbid-pure-static-backend: static only"],
            },
        },
        toolkit=toolkit,
        gaps=[VerificationGap(code="smoke-start", detail="failed to boot")],
    )
    messages = assembler.assemble(turn=0, conversation=[])
    blob = "\n".join(m.content or "" for m in messages)
    assert "skills network" in blob
    assert "forbid-pure-static-backend" in blob
    assert "WORKSPACE" in blob


@pytest.mark.asyncio
async def test_verification_agent_fails_static_app(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)
    toolkit.write_text(
        "index.html",
        "<html><head><title>t</title><style>body{padding:1rem}</style></head>"
        "<body><main><h1>Hi</h1><button data-regent-event='a'>x</button></main></body></html>",
    )
    toolkit.write_text("requirements.txt", "flask\n")
    toolkit.write_text(
        "src/app.py",
        "from flask import Flask, send_from_directory\n"
        "app=Flask(__name__)\n"
        "@app.get('/')\n"
        "def i():\n"
        "  return send_from_directory('.','index.html')\n",
    )
    toolkit.write_text("README.md", "x\n")
    verdict = await VerificationAgent(toolkit).verify(run_smoke=False)
    assert verdict.verdict == "FAIL"
    assert any(g.code == "forbid-pure-static-backend" for g in verdict.gaps)


class _ScriptedProvider:
    """Provider that writes files via tools then stops."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages: list[ChatMessage], tools=None, temperature: float = 0) -> Any:
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="writing product",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={
                                "path": "index.html",
                                "content": (
                                    "<!doctype html><html><head><title>Skills</title>"
                                    "<style>body{max-width:40rem;margin:auto;font-family:sans-serif}"
                                    "main{padding:1rem}</style></head><body><main>"
                                    "<h1>AI Skills Network</h1>"
                                    "<p>Discover skills and connect with peers.</p>"
                                    "<ul><li>Skill A</li><li>Skill B</li><li>Skill C</li></ul>"
                                    "<button data-regent-event='activation'>Join</button>"
                                    "</main></body></html>"
                                ),
                            },
                        ),
                        ToolCall(
                            id="2",
                            name="write_file",
                            arguments={
                                "path": "requirements.txt",
                                "content": "flask\n",
                            },
                        ),
                        ToolCall(
                            id="3",
                            name="write_file",
                            arguments={
                                "path": "src/app.py",
                                "content": (
                                    "from flask import Flask, jsonify\n"
                                    "app = Flask(__name__, static_folder='.', static_url_path='')\n"
                                    "DB = {'skills': [], 'users': []}\n"
                                    "@app.get('/')\n"
                                    "def index():\n"
                                    "    return app.send_static_file('index.html')\n"
                                    "@app.get('/api/skills')\n"
                                    "def skills():\n"
                                    "    return jsonify(DB['skills'])\n"
                                    "@app.post('/api/users')\n"
                                    "def register():\n"
                                    "    user={'id': len(DB['users'])+1}\n"
                                    "    DB['users'].append(user)\n"
                                    "    return jsonify(user)\n"
                                ),
                            },
                        ),
                        ToolCall(
                            id="4",
                            name="write_file",
                            arguments={"path": "README.md", "content": "AI skills app\n"},
                        ),
                    ],
                ),
                usage=ChatUsage(input_tokens=10, output_tokens=20),
                model="fake-agent",
                finish_reason="tool_calls",
            )
        return ChatResponse(
            message=ChatMessage(role="assistant", content="done"),
            usage=ChatUsage(input_tokens=5, output_tokens=5),
            model="fake-agent",
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_agent_runner_writes_files(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path / "ws")
    runner = AgentRunner(
        _ScriptedProvider(),
        toolkit,
        budget=AgentBudget(max_turns=5, max_tokens=10_000, max_wall_seconds=60),
    )
    result = await runner.run(
        {
            "goal_anchor_text": "AI skills network",
            "planned_paths": ["index.html", "src/app.py", "requirements.txt", "README.md"],
            "acceptance_contract": {"first_deliverable": "skill feed"},
        },
        verify=False,
    )
    assert "index.html" in result.files
    assert "src/app.py" in result.files
    assert result.turns >= 1


@pytest.mark.asyncio
async def test_agentic_generator_materializes_artifacts(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    generator = AgenticCodeGenerator(
        _ScriptedProvider(),  # type: ignore[arg-type]
        store,
        workspace_root=tmp_path / "workspaces",
        budget=AgentBudget(max_turns=5, max_tokens=10_000, max_wall_seconds=60),
    )
    # Bypass verification for unit materialization by monkeypatching runner.verify path:
    # Scripted backend still has send_static_file — force verify=False via runner mock.
    # Use generate with a plan; VerificationAgent may FAIL. Patch generator.run verify.
    from regent.agent import generator as gen_mod

    original_run = AgentRunner.run

    async def run_no_verify(
            self, plan, *, prior_gaps=None, verify=True, run_smoke=True, on_turn=None
        ):  # noqa: ANN001
        return await original_run(
            self, plan, prior_gaps=prior_gaps, verify=False, run_smoke=False
        )

    gen_mod.AgentRunner.run = run_no_verify  # type: ignore[method-assign]
    try:
        result = await generator.generate(
            {
                "planned_paths": [
                    "index.html",
                    "src/app.py",
                    "requirements.txt",
                    "README.md",
                ],
                "hypothesis_decision_id": str(uuid.uuid4()),
                "goal_anchor_text": "AI skills",
                "acceptance_contract": {},
            }
        )
    finally:
        gen_mod.AgentRunner.run = original_run  # type: ignore[method-assign]
    assert result.output.generator_ref == "agentic-generation-v1"
    assert len(result.output.changes) >= 3
    assert result.model_ref == "fake-agent"
