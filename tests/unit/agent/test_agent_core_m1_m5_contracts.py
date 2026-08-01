"""M1–M5 contract tests for Agent Core Restoration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from regent.agent.accepted_workspace import (
    verify_promotion_hashes,
    write_accepted_workspace_snapshot,
)
from regent.agent.agent_runner import AgentRunner
from regent.agent.file_manifest import build_workspace_manifest
from regent.agent.repair_policy import plan_repair
from regent.agent.runtime_profile_v1 import (
    CERTIFIED_RUNTIME_PROFILES_V1,
    parse_runtime_profile_v1,
    profile_by_name,
)
from regent.agent.skills import (
    list_builtin_skill_ids,
    load_skill_manifest,
    route_skills_for_gaps,
    select_skills_for_goal,
    skill_ablation_report,
)
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import (
    AgentBudget,
    ArtifactIncompleteError,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    ToolCall,
)
from regent.agent.verification import VerificationAgent, _routes_from_profile_and_criteria
from regent.model import OpenAICompatibleProvider
from regent.model.chat import ChatMessage as ProviderChatMessage


@pytest.mark.asyncio
async def test_m1_2_retries_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate"})
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="m",
            max_http_retries=3,
            retry_deadline_seconds=30,
            client=client,
        )
        # Patch sleep to no-op for speed
        async def _noop(**_kwargs: Any) -> None:
            return None

        provider._sleep_backoff = _noop  # type: ignore[method-assign]
        result = await provider.chat(messages=[ProviderChatMessage(role="user", content="x")])
    assert result.message.content == "ok"
    assert calls["n"] == 3
    assert len(provider.last_http_attempts) == 3


@pytest.mark.asyncio
async def test_m1_2_401_not_retried() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "nope"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="m",
            max_http_retries=3,
            client=client,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await provider.chat(messages=[ProviderChatMessage(role="user", content="x")])
    assert calls["n"] == 1


def test_m1_5_tsx_manifest_includes_thirty_files(tmp_path: Path) -> None:
    for i in range(30):
        (tmp_path / f"Component{i}.tsx").write_text(f"export const C{i}=()=>null\n", encoding="utf-8")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "index.js").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    manifest = build_workspace_manifest(tmp_path)
    assert len(manifest.files) == 30
    assert all(p.endswith(".tsx") for p in manifest.files)
    assert manifest.integrity_ok
    skipped_reasons = {e.reason for e in manifest.entries if not e.included}
    assert any(r and "excluded_dir" in r for r in skipped_reasons)
    assert any(r and "secret" in r for r in skipped_reasons)


def test_m1_5_truncation_fails_integrity(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text("x" * 250_000, encoding="utf-8")
    (tmp_path / "ok.py").write_text("print(1)\n", encoding="utf-8")
    manifest = build_workspace_manifest(tmp_path, max_file_bytes=200_000)
    assert not manifest.integrity_ok
    assert manifest.truncated


@pytest.mark.asyncio
async def test_m1_3_soft_stop_without_submit_is_incomplete(tmp_path: Path) -> None:
    class _Prov:
        async def chat(self, **kwargs: Any) -> Any:
            return ChatResponse(
                message=ChatMessage(role="assistant", content="stopped", tool_calls=[]),
                usage=ChatUsage(1, 1),
                model="m",
                finish_reason="stop",
            )

    runner = AgentRunner(
        _Prov(),
        WorkspaceToolkit(tmp_path),
        budget=AgentBudget(max_turns=3, max_tokens=10_000, max_wall_seconds=30),
    )
    with pytest.raises(ArtifactIncompleteError):
        await runner.run({"goal_anchor_text": "x"}, verify=False)


def test_m2_routes_no_unconditional_health() -> None:
    profile = profile_by_name("flask-web-v1")
    assert profile is not None
    routes = _routes_from_profile_and_criteria(profile, {})
    assert "/health" not in routes
    assert "/" in routes
    fa = profile_by_name("fastapi-web-v1")
    assert fa is not None
    routes2 = _routes_from_profile_and_criteria(fa, {})
    assert "/health" in routes2


def test_m2_certified_profiles_exist() -> None:
    names = {p.name for p in CERTIFIED_RUNTIME_PROFILES_V1}
    assert {"static-web-v1", "flask-web-v1", "fastapi-web-v1"} <= names
    parsed = parse_runtime_profile_v1(profile_by_name("flask-web-v1").as_dict())  # type: ignore[union-attr]
    assert parsed is not None
    assert parsed.schema_version == "runtime-profile/v1"


@pytest.mark.asyncio
async def test_m2_4_require_tests_fails_without_tests(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)
    toolkit.write_text("src/app.py", "from flask import Flask\napp=Flask(__name__)\n")
    toolkit.write_text("requirements.txt", "flask\n")
    toolkit.write_text(
        "index.html",
        "<html><body><main><h1>Hi</h1>"
        "<button data-regent-event='a'>x</button></main></body></html>",
    )
    profile = profile_by_name("flask-web-v1")
    verdict = await VerificationAgent(toolkit, runtime_profile=profile).verify(
        run_smoke=False
    )
    assert not verdict.passed
    assert any(g.code in {"TEST_FAILED", "TEST_COMMAND_MISSING"} or "test" in g.code.lower() for g in verdict.gaps)


def test_m3_edit_file_unique_match_and_conflict(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)
    toolkit.write_text("a.py", "x = 1\nx = 1\n")
    with pytest.raises(ValueError, match="matches 2"):
        toolkit.edit_file("a.py", old_text="x = 1", new_text="x = 2")
    toolkit.write_text("b.py", "hello\n")
    toolkit.edit_file("b.py", old_text="hello", new_text="world")
    assert toolkit.read_text("b.py") == "world\n"


def test_m3_read_artifact_roundtrip(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)
    meta = toolkit.register_artifact(ref="log1", text="traceback here")
    text = toolkit.read_artifact(path=meta["path"])
    assert "traceback" in text
    with pytest.raises(FileNotFoundError):
        toolkit.read_artifact(uri="missing-ref")


def test_m3_repair_policy_no_temp_ladder() -> None:
    plan = plan_repair("TEST_FAILED", repeat_count=1)
    assert plan.temperature == 0.0
    assert plan.max_extra_turns > 0
    stop = plan_repair("BUDGET_EXHAUSTED")
    assert stop.max_extra_turns == 0


def test_m4_accepted_snapshot_and_hash_gate(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("app=None\n", encoding="utf-8")
    snap = write_accepted_workspace_snapshot(
        ws, tmp_path / "store", profile_hash="p1", verification_hash="v1"
    )
    assert Path(snap.root).is_dir()
    errs = verify_promotion_hashes(
        manifest_hash=snap.manifest_hash,
        profile_hash=snap.profile_hash,
        verification_hash=snap.verification_hash,
        preview_deployment_hash="d1",
        expected={
            "manifest_hash": snap.manifest_hash,
            "profile_hash": "p1",
            "verification_hash": "v1",
            "preview_deployment_hash": "d1",
        },
    )
    assert errs == []
    bad = verify_promotion_hashes(
        manifest_hash="x",
        profile_hash="p1",
        verification_hash="v1",
        preview_deployment_hash="d1",
        expected={
            "manifest_hash": snap.manifest_hash,
            "profile_hash": "p1",
            "verification_hash": "v1",
            "preview_deployment_hash": "d1",
        },
    )
    assert bad


def test_m5_three_skills_and_routing() -> None:
    ids = list_builtin_skill_ids()
    assert set(ids) >= {"runtime-contract", "web-app-scaffold", "test-harness"}
    for sid in ("runtime-contract", "web-app-scaffold", "test-harness"):
        m = load_skill_manifest(sid)
        assert m.content_hash
        assert m.guidance
    selected = select_skills_for_goal("build a flask todo crud app with sqlite")
    assert any(s.skill_id == "web-app-scaffold" for s in selected)
    routed = route_skills_for_gaps(["TEST_FAILED", "SMOKE_FAILED"])
    assert {s.skill_id for s in routed} >= {"test-harness", "runtime-contract"}
    report = skill_ablation_report(on_pass=3, on_total=5, off_pass=1, off_total=5)
    assert report["engineering_gate_only"] is True
    assert report["delta"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_m3_1_non_recursive_repair_same_trajectory(tmp_path: Path) -> None:
    toolkit = WorkspaceToolkit(tmp_path)

    class _Prov:
        def __init__(self) -> None:
            self.n = 0

        async def chat(self, *, messages, tools=None, temperature: float = 0) -> Any:
            self.n += 1
            # Ensure conversation continuity: later turns see prior user repair message.
            if self.n >= 3:
                assert any(m.role == "user" and "Verification failed" in (m.content or "") for m in messages)
            if self.n == 1:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="w",
                        tool_calls=[
                            ToolCall(
                                id="1",
                                name="write_file",
                                arguments={"path": "a.py", "content": "x=1\n"},
                            )
                        ],
                    ),
                    usage=ChatUsage(10, 5),
                    model="m",
                    finish_reason="tool_calls",
                )
            if self.n == 2:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="s",
                        tool_calls=[
                            ToolCall(id="2", name="submit", arguments={"summary": "v1"})
                        ],
                    ),
                    usage=ChatUsage(10, 5),
                    model="m",
                    finish_reason="tool_calls",
                )
            if self.n == 3:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="fix",
                        tool_calls=[
                            ToolCall(
                                id="3",
                                name="write_file",
                                arguments={"path": "b.py", "content": "y=2\n"},
                            )
                        ],
                    ),
                    usage=ChatUsage(10, 5),
                    model="m",
                    finish_reason="tool_calls",
                )
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="s2",
                    tool_calls=[
                        ToolCall(id="4", name="submit", arguments={"summary": "v2"})
                    ],
                ),
                usage=ChatUsage(10, 5),
                model="m",
                finish_reason="tool_calls",
            )

    verify_calls = {"n": 0}

    class _FakeVerify:
        def __init__(self, toolkit: WorkspaceToolkit, **kwargs: Any) -> None:
            self._toolkit = toolkit

        async def verify(self, **kwargs: Any) -> Any:
            from regent.agent.types import VerificationGap, VerificationVerdict

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
        result = await AgentRunner(
            _Prov(),
            toolkit,
            budget=AgentBudget(max_turns=10, max_tokens=50_000, max_wall_seconds=60),
        ).run(
            {"goal_anchor_text": "x"},
            verify=True,
            run_smoke=False,
            _nested_repair_budget=1,
        )
    finally:
        runner_mod.VerificationAgent = original  # type: ignore[misc]

    assert result.submitted
    assert result.verification and result.verification.passed
    assert result.ledger.repair_rounds == 1
    assert "b.py" in result.files
