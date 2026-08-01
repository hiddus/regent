"""M0/M1-1/M1-4 contract tests for Agent Core Restoration first batch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from regent.agent.agent_runner import AgentRunner
from regent.agent.primary_failure import (
    PRIMARY_FAILURE_CODES,
    PrimaryFailureCode,
    normalize_primary_failure_code,
)
from regent.agent.run_ledger import AgentRunLedger
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import (
    AgentBudget,
    BudgetExhaustedError,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    ToolCall,
    VerificationGap,
)
from regent.application.delivery_rejection import DeliveryRejection
from regent.application.eval_harness_service import EvalHarnessService
from regent.domain.errors import DomainError, ErrorCode
from regent.model import (
    ModelTruncatedError,
    OpenAICompatibleProvider,
    ToolCallInvalidError,
)
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
TASK_SET = ROOT / "fixtures" / "agent_core_m0_task_set_v1.json"
TASK_HASH = ROOT / "fixtures" / "agent_core_m0_task_set_v1.sha256"
RECORDINGS = ROOT / "fixtures" / "provider_recordings"


def test_m0_frozen_task_set_inventory_and_hash() -> None:
    payload = json.loads(TASK_SET.read_text(encoding="utf-8"))
    tasks = payload["tasks"]
    assert len(tasks) == 12
    assert payload["archetype"] == "light-backend-web-app"
    assert set(payload["debug_holdout"]) == {"m0-11", "m0-12"}
    ids = [t["id"] for t in tasks]
    assert ids == [f"m0-{i:02d}" for i in range(1, 13)]
    for task in tasks:
        assert task["difficulty"] in {"easy", "medium", "hard"}
        assert int(task["timeout_seconds"]) > 0
        assert int(task["budget_tokens"]) > 0
        assert "success_criteria" in task
    digest = hashlib.sha256(TASK_SET.read_bytes()).hexdigest()
    assert TASK_HASH.read_text(encoding="utf-8").strip() == digest


def test_m0_primary_failure_taxonomy_contract() -> None:
    required = {
        "MODEL_TRUNCATED",
        "TOOL_CALL_INVALID",
        "ARTIFACT_INCOMPLETE",
        "STATIC_FAILED",
        "TEST_FAILED",
        "START_FAILED",
        "SMOKE_FAILED",
        "BUDGET_EXHAUSTED",
        "PREVIEW_FAILED",
    }
    assert required <= PRIMARY_FAILURE_CODES
    assert normalize_primary_failure_code("EXHAUSTED_BUDGET") is PrimaryFailureCode.BUDGET_EXHAUSTED
    assert normalize_primary_failure_code("project-tests") is PrimaryFailureCode.TEST_FAILED
    assert normalize_primary_failure_code("totally-unknown") is PrimaryFailureCode.UNKNOWN
    # Unknown is a failure, never a success code.
    assert "SUCCESS" not in PRIMARY_FAILURE_CODES


def test_m0_run_ledger_sums_across_repair_rounds() -> None:
    round1 = AgentRunLedger(input_tokens=100, output_tokens=40, tool_invocations=3, turns=2)
    round2 = AgentRunLedger(
        input_tokens=80, output_tokens=20, tool_invocations=2, turns=1, repair_rounds=1
    )
    total = AgentRunLedger()
    total.merge(round1)
    total.merge(round2)
    assert total.input_tokens == 180
    assert total.output_tokens == 60
    assert total.tool_invocations == 5
    assert total.turns == 3
    assert total.repair_rounds == 1
    assert total.total_tokens == 240


@pytest.mark.asyncio
async def test_eval_load_frozen_task_set_fail_closed() -> None:
    svc = EvalHarnessService(MagicMock())
    with pytest.raises(DomainError) as exc:
        await svc.load_frozen_task_set("nonexistent-artifact-path")
    assert exc.value.code is ErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_provider_replay_length_is_model_truncated() -> None:
    sample = json.loads((RECORDINGS / "length_truncation.json").read_text(encoding="utf-8"))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sample["response"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="replay-model",
            client=client,
        )
        with pytest.raises(ModelTruncatedError) as exc:
            await provider.chat(messages=[ChatMessage(role="user", content="go")])
    assert exc.value.failure_code == "MODEL_TRUNCATED"


@pytest.mark.asyncio
async def test_provider_replay_malformed_tool_args() -> None:
    sample = json.loads((RECORDINGS / "malformed_tool_args.json").read_text(encoding="utf-8"))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sample["response"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="replay-model",
            client=client,
        )
        with pytest.raises(ToolCallInvalidError) as exc:
            await provider.chat(messages=[ChatMessage(role="user", content="go")])
    assert exc.value.failure_code == "TOOL_CALL_INVALID"


@pytest.mark.asyncio
async def test_provider_replay_normal_tool_call_and_max_tokens() -> None:
    sample = json.loads((RECORDINGS / "normal_tool_call.json").read_text(encoding="utf-8"))
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=sample["response"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="replay-model",
            max_output_tokens=2048,
            client=client,
        )
        result = await provider.chat(messages=[ChatMessage(role="user", content="go")])
    assert seen.get("max_tokens") == 2048
    assert result.message.tool_calls
    assert result.message.tool_calls[0].name == "write_file"
    assert result.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_m1_4_budget_exhaust_saves_diagnostics_no_promote(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)
    toolkit.write_text("src/app.py", "print('partial')\n")

    class _ExhaustProvider:
        async def chat(self, *, messages, tools=None, temperature: float = 0) -> Any:
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="keep going",
                    tool_calls=[
                        ToolCall(
                            id="t1",
                            name="write_file",
                            arguments={"path": "README.md", "content": "x\n"},
                        )
                    ],
                ),
                usage=ChatUsage(input_tokens=10, output_tokens=5),
                model="m",
                finish_reason="tool_calls",
            )

    runner = AgentRunner(
        _ExhaustProvider(),
        toolkit,
        budget=AgentBudget(max_turns=2, max_tokens=200_000, max_wall_seconds=900),
    )
    with pytest.raises(BudgetExhaustedError) as exc:
        await runner.run({"goal_anchor_text": "x"}, verify=False)
    assert exc.value.failure_code == "BUDGET_EXHAUSTED"
    assert exc.value.diagnostic_manifest.get("promote_allowed") is False
    diag = tmp_path / ".regent_budget_exhausted.json"
    assert diag.is_file()
    body = json.loads(diag.read_text(encoding="utf-8"))
    assert body["primary_failure_code"] == "BUDGET_EXHAUSTED"
    assert body["promote_allowed"] is False
    assert (tmp_path / ".regent_agent_transcript.json").is_file()
    assert (tmp_path / ".regent_run_ledger.json").is_file()
    assert "src/app.py" in exc.value.files or (tmp_path / "README.md").exists()


@pytest.mark.asyncio
async def test_m0_3_nested_repair_ledger_accumulates(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)

    class _TwoPhaseProvider:
        def __init__(self) -> None:
            self.n = 0

        async def chat(self, *, messages, tools=None, temperature: float = 0) -> Any:
            self.n += 1
            if self.n == 1:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="draft",
                        tool_calls=[
                            ToolCall(
                                id="1",
                                name="write_file",
                                arguments={"path": "a.py", "content": "x=1\n"},
                            )
                        ],
                    ),
                    usage=ChatUsage(input_tokens=100, output_tokens=20),
                    model="m",
                    finish_reason="tool_calls",
                )
            if self.n == 2:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="submit round1",
                        tool_calls=[
                            ToolCall(
                                id="s1",
                                name="submit",
                                arguments={"summary": "round1"},
                            )
                        ],
                    ),
                    usage=ChatUsage(input_tokens=50, output_tokens=10),
                    model="m",
                    finish_reason="tool_calls",
                )
            if self.n == 3:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="repair",
                        tool_calls=[
                            ToolCall(
                                id="2",
                                name="write_file",
                                arguments={"path": "b.py", "content": "y=2\n"},
                            )
                        ],
                    ),
                    usage=ChatUsage(input_tokens=80, output_tokens=15),
                    model="m",
                    finish_reason="tool_calls",
                )
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="submit round2",
                    tool_calls=[
                        ToolCall(
                            id="s2",
                            name="submit",
                            arguments={"summary": "round2"},
                        )
                    ],
                ),
                usage=ChatUsage(input_tokens=40, output_tokens=5),
                model="m",
                finish_reason="tool_calls",
            )

    verify_calls = {"n": 0}

    class _FakeVerify:
        def __init__(self, toolkit: WorkspaceToolkit, **kwargs: Any) -> None:
            self._toolkit = toolkit

        async def verify(self, **kwargs: Any) -> Any:
            from regent.agent.types import VerificationVerdict

            verify_calls["n"] += 1
            if verify_calls["n"] == 1:
                return VerificationVerdict(
                    verdict="FAIL",
                    gaps=[VerificationGap(code="STATIC_FAILED", detail="need more")],
                )
            return VerificationVerdict(verdict="PASS", gaps=[])

    import regent.agent.agent_runner as runner_mod

    original = runner_mod.VerificationAgent
    runner_mod.VerificationAgent = _FakeVerify  # type: ignore[misc,assignment]
    try:
        runner = AgentRunner(
            _TwoPhaseProvider(),
            toolkit,
            budget=AgentBudget(max_turns=10, max_tokens=200_000, max_wall_seconds=900),
        )
        result = await runner.run(
            {"goal_anchor_text": "x", "acceptance_contract": {}},
            verify=True,
            run_smoke=False,
            _nested_repair_budget=1,
        )
    finally:
        runner_mod.VerificationAgent = original  # type: ignore[misc]

    assert result.ledger.input_tokens == 100 + 50 + 80 + 40
    assert result.ledger.output_tokens == 20 + 10 + 15 + 5
    assert result.input_tokens == result.ledger.input_tokens
    assert result.verification is not None and result.verification.passed
    assert verify_calls["n"] == 2


@pytest.mark.asyncio
async def test_generator_budget_exhaust_maps_to_delivery_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from regent.agent import generator as gen_mod
    from regent.agent.generator import AgenticCodeGenerator
    from regent.infrastructure.artifact_store import FileArtifactStore

    class _BoomRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._toolkit = args[1] if len(args) > 1 else kwargs.get("toolkit")

        async def run(self, *args: Any, **kwargs: Any) -> Any:
            root = self._toolkit.root
            (root / "partial.py").write_text("x\n", encoding="utf-8")
            (root / ".regent_budget_exhausted.json").write_text(
                json.dumps(
                    {
                        "primary_failure_code": "BUDGET_EXHAUSTED",
                        "promote_allowed": False,
                        "reason": "max_turns",
                    }
                ),
                encoding="utf-8",
            )
            raise BudgetExhaustedError(
                "max_turns=1 exhausted",
                diagnostic_manifest={"promote_allowed": False},
                files={"partial.py": "x\n"},
            )

    class _NoopSandbox:
        async def exec_in_workspace(self, *args: Any, **kwargs: Any) -> str:
            return ""

    monkeypatch.setattr(gen_mod, "AgentRunner", _BoomRunner)
    monkeypatch.setattr(gen_mod, "build_agent_sandbox", lambda: _NoopSandbox())

    class _Prov:
        async def generate_structured(self, **kwargs: Any) -> Any:
            raise AssertionError("unused")

        async def chat(self, **kwargs: Any) -> Any:
            raise AssertionError("unused")

    artifacts = FileArtifactStore(tmp_path / "arts")
    gen = AgenticCodeGenerator(_Prov(), artifacts, workspace_root=tmp_path)
    with pytest.raises(DeliveryRejection) as exc:
        await gen.generate({"goal_anchor_text": "g", "planned_paths": ["partial.py"]})
    assert any(r.startswith("BUDGET_EXHAUSTED:") for r in exc.value.reasons)
    assert exc.value.gap_kind == "BUDGET_EXHAUSTED"
    assert exc.value.draft_uri
