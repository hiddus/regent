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
    # Ship-first: do not implicitly probe /health for fastapi either.
    assert "/health" not in routes2
    assert "/" in routes2


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
    # Mild diversity only on repeated gap — not a multi-step ladder.
    retry = plan_repair("TEST_FAILED", repeat_count=2)
    assert retry.temperature == 0.2
    assert retry.max_extra_turns == plan.max_extra_turns


def test_p0_4_no_recursive_self_run_in_source() -> None:
    """Single-generation loop must never cold-restart via recursive self.run()."""
    import inspect
    import re

    from regent.agent import agent_runner as mod

    src = inspect.getsource(mod.AgentRunner.run)
    # Match call sites only (not comments mentioning self.run).
    calls = re.findall(r"(?<![\"'#])\bawait\s+self\.run\s*\(", src)
    calls += re.findall(r"(?<![\"'#.\w])self\.run\s*\(", src)
    # Filter comment-only lines.
    live = []
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        if re.search(r"\bawait\s+self\.run\s*\(", code) or re.search(
            r"(?<!\.)\bself\.run\s*\(", code
        ):
            # Method definition line is fine.
            if re.search(r"\basync\s+def\s+run\s*\(", code):
                continue
            live.append(line.strip())
    assert live == [], f"recursive self.run calls found: {live}"


@pytest.mark.asyncio
async def test_p0_4_identical_gap_fingerprint_stops_loop(tmp_path: Path) -> None:
    """Same verification gaps after a repair must not thrash until max_turns."""
    toolkit = WorkspaceToolkit(tmp_path)
    temps: list[float] = []

    class _Prov:
        def __init__(self) -> None:
            self.n = 0

        async def chat(self, *, messages, tools=None, temperature: float = 0) -> Any:
            self.n += 1
            temps.append(float(temperature))
            # Alternating write+submit forever if not stopped.
            if self.n % 2 == 1:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="w",
                        tool_calls=[
                            ToolCall(
                                id=f"w{self.n}",
                                name="write_file",
                                arguments={"path": "a.py", "content": "x=1\n"},
                            )
                        ],
                    ),
                    usage=ChatUsage(5, 2),
                    model="m",
                    finish_reason="tool_calls",
                )
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="s",
                    tool_calls=[
                        ToolCall(
                            id=f"s{self.n}",
                            name="submit",
                            arguments={"summary": "same"},
                        )
                    ],
                ),
                usage=ChatUsage(5, 2),
                model="m",
                finish_reason="tool_calls",
            )

    class _AlwaysFail:
        def __init__(self, toolkit: WorkspaceToolkit, **kwargs: Any) -> None:
            pass

        async def verify(self, **kwargs: Any) -> Any:
            from regent.agent.types import VerificationGap, VerificationVerdict

            return VerificationVerdict(
                verdict="FAIL",
                gaps=[VerificationGap(code="STATIC_FAILED", detail="stuck")],
            )

    import regent.agent.agent_runner as runner_mod

    original = runner_mod.VerificationAgent
    runner_mod.VerificationAgent = _AlwaysFail  # type: ignore[misc,assignment]
    try:
        result = await AgentRunner(
            _Prov(),
            toolkit,
            budget=AgentBudget(max_turns=40, max_tokens=200_000, max_wall_seconds=120),
        ).run(
            {"goal_anchor_text": "x"},
            verify=True,
            run_smoke=False,
            _nested_repair_budget=4,
        )
    finally:
        runner_mod.VerificationAgent = original  # type: ignore[misc]

    assert result.submitted
    assert result.verification is not None and not result.verification.passed
    # First fail opens repair; second identical fail stops — not 40-turn thrash.
    assert result.ledger.repair_rounds == 1
    assert any("identical_gap_fingerprint_stop" in n for n in result.ledger.notes)
    assert len(temps) < 12
    # Second repair attempt would use mild temperature; we stop before needing many.
    assert any(t == 0.0 for t in temps)


@pytest.mark.asyncio
async def test_p0_4_repair_phase_turn_cap_without_submit(tmp_path: Path) -> None:
    """max_extra_turns must bound tool thrashing after a gap message."""
    toolkit = WorkspaceToolkit(tmp_path)

    class _Prov:
        def __init__(self) -> None:
            self.n = 0

        async def chat(self, *, messages, tools=None, temperature: float = 0) -> Any:
            self.n += 1
            if self.n == 1:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="s",
                        tool_calls=[
                            ToolCall(id="s1", name="submit", arguments={"summary": "v1"})
                        ],
                    ),
                    usage=ChatUsage(5, 2),
                    model="m",
                    finish_reason="tool_calls",
                )
            # After gap: never submit — only write forever.
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="w",
                    tool_calls=[
                        ToolCall(
                            id=f"w{self.n}",
                            name="write_file",
                            arguments={"path": f"f{self.n}.py", "content": "x=1\n"},
                        )
                    ],
                ),
                usage=ChatUsage(5, 2),
                model="m",
                finish_reason="tool_calls",
            )

    class _FailOnce:
        def __init__(self, toolkit: WorkspaceToolkit, **kwargs: Any) -> None:
            pass

        async def verify(self, **kwargs: Any) -> Any:
            from regent.agent.types import VerificationGap, VerificationVerdict

            return VerificationVerdict(
                verdict="FAIL",
                gaps=[VerificationGap(code="STATIC_FAILED", detail="fix")],
            )

    import regent.agent.agent_runner as runner_mod
    from regent.agent.types import BudgetExhaustedError

    original = runner_mod.VerificationAgent
    runner_mod.VerificationAgent = _FailOnce  # type: ignore[misc,assignment]
    prov = _Prov()
    try:
        with pytest.raises(BudgetExhaustedError) as exc:
            await AgentRunner(
                prov,
                toolkit,
                budget=AgentBudget(max_turns=40, max_tokens=200_000, max_wall_seconds=120),
            ).run(
                {"goal_anchor_text": "x"},
                verify=True,
                run_smoke=False,
                _nested_repair_budget=2,
            )
    finally:
        runner_mod.VerificationAgent = original  # type: ignore[misc]

    assert "repair phase max_extra_turns" in exc.value.reason
    # 1 submit + <=6 repair writes (STATIC max_extra_turns), never 40-turn thrash.
    assert prov.n <= 1 + 6
    assert len(list(tmp_path.glob("f*.py"))) <= 6


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


def test_p0_5_revise_clones_accepted_not_draft(tmp_path: Path) -> None:
    from regent.agent.accepted_workspace import clone_accepted_snapshot

    accepted = tmp_path / "accepted_src"
    draft = tmp_path / "failed_draft"
    accepted.mkdir()
    draft.mkdir()
    (accepted / "src").mkdir()
    (accepted / "src" / "app.py").write_text("ACCEPTED=1\n", encoding="utf-8")
    (draft / "src").mkdir()
    (draft / "src" / "app.py").write_text("DRAFT=1\n", encoding="utf-8")
    snap = write_accepted_workspace_snapshot(
        accepted, tmp_path / "store", profile_hash="p", verification_hash="v"
    )
    dest = tmp_path / "revise"
    clone_accepted_snapshot(snap.uri, dest)
    assert (dest / "src" / "app.py").read_text(encoding="utf-8") == "ACCEPTED=1\n"
    assert "DRAFT" not in (dest / "src" / "app.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_p0_5_runtime_preview_materializes_and_requires_entry(tmp_path: Path) -> None:
    from regent.agent.runtime_profile_v1 import RuntimeProfileV1, RUNTIME_PROFILE_SCHEMA_VERSION
    from regent.application.p1_ports import DeploymentRequest
    from regent.infrastructure.runtime_preview import RuntimePreviewDeploymentProvider

    class _Static:
        async def deploy(self, request: DeploymentRequest) -> Any:
            raise AssertionError("static path must not run for runtime preview")

    ws = tmp_path / "artifact"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("app = object()\n", encoding="utf-8")
    (ws / "serve.py").write_text(
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "PORT = int(os.environ['PORT'])\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, *a): pass\n"
        "HTTPServer(('127.0.0.1', PORT), H).serve_forever()\n",
        encoding="utf-8",
    )
    profile = RuntimeProfileV1(
        name="test-runtime-preview",
        version="1",
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape="flask-web",
        entry_module="src.app",
        entry_object="app",
        start_command="python serve.py",
        workdir=".",
        health_routes=(),
        readiness_routes=("/",),
        smoke_routes=("/",),
        install_command=None,
        test_command=None,
        require_tests=False,
        allow_network=False,
        preview_type="runtime",
        network_allowlist=(),
    )
    provider = RuntimePreviewDeploymentProvider(
        tmp_path / "previews",
        static_provider=_Static(),
        base_url="http://preview.test",
        readiness_timeout_seconds=15.0,
    )
    ok = await provider.deploy(
        DeploymentRequest(
            build_artifact_uri=str(ws),
            environment="preview",
            idempotency_key="k1",
            correlation_id="c1",
            acceptance_contract={"runtime_profile": profile.as_dict()},
        )
    )
    assert ok.status == "SUCCEEDED"
    assert ok.evidence.get("profile_hash") == profile.content_hash
    assert ok.evidence.get("live_preview") is True
    assert ok.evidence.get("pid")
    assert Path(str(ok.evidence.get("workspace_path"))).is_dir()
    assert str(ok.endpoint or "").startswith("http://127.0.0.1:")
    await provider.rollback("k1", "corr-roll")

    missing = tmp_path / "empty"
    missing.mkdir()
    bad = await provider.deploy(
        DeploymentRequest(
            build_artifact_uri=str(missing),
            environment="preview",
            idempotency_key="k2",
            correlation_id="c2",
            acceptance_contract={"runtime_profile": profile.as_dict()},
        )
    )
    assert bad.status == "FAILED"
    assert bad.evidence.get("failure_code") == "PREVIEW_FAILED"


def test_m5_three_skills_and_routing() -> None:
    ids = list_builtin_skill_ids()
    assert set(ids) >= {
        "runtime-contract",
        "web-app-scaffold",
        "test-harness",
        "persistence",
        "http-api",
        "evidence",
        "ui",
    }
    assert len(ids) >= 7
    for sid in (
        "runtime-contract",
        "web-app-scaffold",
        "test-harness",
        "persistence",
        "http-api",
        "evidence",
        "ui",
    ):
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


def test_r1_recoverable_snapshot_clone(tmp_path: Path) -> None:
    from regent.agent.accepted_workspace import (
        clone_accepted_snapshot,
        write_recoverable_workspace_snapshot,
    )

    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "src").mkdir()
    (draft / "src" / "app.py").write_text("RECOVERABLE=1\n", encoding="utf-8")
    uri = write_recoverable_workspace_snapshot(draft, tmp_path / "store", reason="test")
    dest = tmp_path / "revise"
    clone_accepted_snapshot(uri, dest)
    assert (dest / "src" / "app.py").read_text(encoding="utf-8") == "RECOVERABLE=1\n"


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
