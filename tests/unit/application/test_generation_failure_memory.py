"""Tests for generation failure memory binding (run id / review pointer)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.generation_service import GenerationService
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import GenerationPlanModel, GenerationRunModel


def _session_factory(session: AsyncMock) -> MagicMock:
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    return MagicMock(return_value=session_context)


@pytest.mark.asyncio
async def test_claim_injects_generation_run_id() -> None:
    run_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    run = GenerationRunModel(
        id=run_id,
        plan_id=plan_id,
        attempt=1,
        status="REQUESTED",
        version=0,
        idempotency_key="k",
        correlation_id="c",
    )
    plan = GenerationPlanModel(
        id=plan_id,
        requirement_revision_id=uuid.uuid4(),
        capability_resolution_plan_id=uuid.uuid4(),
        status="FROZEN",
        version=1,
        input_digest="a" * 64,
        contract_json={"planned_paths": ["src/app.py"], "acceptance_contract": {}},
        architecture_summary="x",
        component_plan=[],
        created_by="t",
        correlation_id="c",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=run)
    session.get = AsyncMock(return_value=plan)
    svc = GenerationService(_session_factory(session), MagicMock(), MagicMock())

    payload = await svc._claim(run_id)
    assert payload["generation_run_id"] == str(run_id)
    assert payload["plan_id"] == str(plan_id)
    assert run.status == "GENERATING"
    assert plan.status == "EXECUTING"


@pytest.mark.asyncio
async def test_fail_stores_specific_failure_code() -> None:
    run_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    run = GenerationRunModel(
        id=run_id,
        plan_id=plan_id,
        attempt=1,
        status="GENERATING",
        version=1,
        idempotency_key="k",
        correlation_id="c",
    )
    plan = GenerationPlanModel(
        id=plan_id,
        requirement_revision_id=uuid.uuid4(),
        capability_resolution_plan_id=uuid.uuid4(),
        status="EXECUTING",
        version=1,
        input_digest="b" * 64,
        contract_json={},
        architecture_summary="x",
        component_plan=[],
        created_by="t",
        correlation_id="c",
    )
    session = AsyncMock()
    session.get = AsyncMock(side_effect=[run, plan])
    svc = GenerationService(_session_factory(session), MagicMock(), MagicMock())
    await svc._fail(run_id, failure_code=ErrorCode.POLICY_DENIED.value)
    assert run.status == "FAILED"
    assert run.failure_code == "POLICY_DENIED"
    assert plan.status == "FAILED"


def test_local_path_from_uri_roundtrip(tmp_path) -> None:
    from regent.application.execution_orchestrator import ExecutionOrchestrator

    target = tmp_path / "draft"
    target.mkdir()
    uri = target.resolve().as_uri()
    got = ExecutionOrchestrator._local_path_from_uri(uri)
    assert got is not None
    assert got.resolve() == target.resolve()
    assert ExecutionOrchestrator._local_path_from_uri("") is None
