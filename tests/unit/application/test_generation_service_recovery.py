"""COMPLETED plan + GenerationRunRequested / recovery must reopen, not dead-letter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.generation_service import (
    CreateGenerationPlan,
    GenerationService,
    RequestGenerationRun,
)
from regent.application.p1_contracts import GenerationPlanContract, canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import GenerationPlanModel, GenerationRunModel

_HASH = "0" * 64


def _contract(**acceptance: object) -> GenerationPlanContract:
    return GenerationPlanContract(
        goal_spec_hash=_HASH,
        hypothesis_decision_id=uuid.uuid4(),
        requirement_revision_hash=_HASH,
        capability_resolution_hash=_HASH,
        runtime_profile_hash=_HASH,
        evidence_bundle_digest=_HASH,
        generator_ref="artifact-backed-code-generator-v1",
        model_ref="p1-model",
        prompt_version="code-generation-v1",
        verification_commands=["python -c 'import app'"],
        acceptance_contract=dict(acceptance) if acceptance else {"org_key": "default"},
    )


def _session_factory(session: AsyncMock) -> MagicMock:
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    return MagicMock(return_value=session_context)


def _completed_plan(*, digest: str | None = None) -> GenerationPlanModel:
    contract = _contract()
    return GenerationPlanModel(
        id=uuid.uuid4(),
        requirement_revision_id=uuid.uuid4(),
        capability_resolution_plan_id=uuid.uuid4(),
        status="COMPLETED",
        version=3,
        input_digest=digest or canonical_hash(contract),
        contract_json=contract.model_dump(mode="json"),
        architecture_summary="summary",
        component_plan=[{"name": "app", "type": "web"}],
        created_by="test",
        correlation_id="corr",
    )


@pytest.mark.asyncio
async def test_create_plan_reopens_completed_on_digest_hit() -> None:
    contract = _contract()
    plan = _completed_plan(digest=canonical_hash(contract))
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=plan)
    session.flush = AsyncMock()
    svc = GenerationService(_session_factory(session), MagicMock(), MagicMock())

    result = await svc.create_plan(
        CreateGenerationPlan(
            requirement_revision_id=plan.requirement_revision_id,
            capability_resolution_plan_id=plan.capability_resolution_plan_id,
            contract=contract,
            architecture_summary="summary",
            component_plan=[{"name": "app", "type": "web"}],
            actor="test",
            correlation_id="corr",
        )
    )

    assert result is plan
    assert plan.status == "FROZEN"
    assert plan.version >= 4
    assert "requirements.txt" in (plan.contract_json.get("planned_paths") or [])
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_request_run_reopens_completed_plan_for_new_idempotency() -> None:
    """Recovery uses a new idempotency key against a digest-hit COMPLETED plan."""
    plan = _completed_plan()
    plan.status = "COMPLETED"
    session = AsyncMock()
    # No existing run by idempotency; then get plan; then max attempt.
    session.scalar = AsyncMock(side_effect=[None, 1])
    session.get = AsyncMock(return_value=plan)
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = GenerationService(_session_factory(session), MagicMock(), MagicMock())

    run = await svc.request_run(
        RequestGenerationRun(
            plan_id=plan.id,
            idempotency_key="generation-delivery-recovery:attempt-1",
            correlation_id="corr",
        )
    )

    assert plan.status == "FROZEN"
    assert plan.version >= 4
    assert "requirements.txt" in (plan.contract_json.get("planned_paths") or [])
    assert run.status == "REQUESTED"
    assert run.plan_id == plan.id
    assert run.attempt == 2
    assert run.idempotency_key == "generation-delivery-recovery:attempt-1"
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_request_run_completed_idempotency_still_returns_existing() -> None:
    plan_id = uuid.uuid4()
    existing = GenerationRunModel(
        id=uuid.uuid4(),
        plan_id=plan_id,
        attempt=1,
        status="COMPLETED",
        version=3,
        idempotency_key="same-key",
        correlation_id="corr",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)
    svc = GenerationService(_session_factory(session), MagicMock(), MagicMock())

    result = await svc.request_run(
        RequestGenerationRun(
            plan_id=plan_id,
            idempotency_key="same-key",
            correlation_id="corr",
        )
    )

    assert result is existing
    assert result.status == "COMPLETED"
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_request_run_rejects_non_runnable_plan_status() -> None:
    plan = _completed_plan()
    plan.status = "CANCELLED"
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.get = AsyncMock(return_value=plan)
    svc = GenerationService(_session_factory(session), MagicMock(), MagicMock())

    with pytest.raises(DomainError) as exc:
        await svc.request_run(
            RequestGenerationRun(
                plan_id=plan.id,
                idempotency_key="new-key",
                correlation_id="corr",
            )
        )
    assert exc.value.code == ErrorCode.INVALID_STATE
    assert "frozen generation plan is required" in str(exc.value)


@pytest.mark.asyncio
async def test_create_plan_then_request_run_recovery_path() -> None:
    """End-to-end of the recovery handshake: digest hit COMPLETED → new REQUESTED run."""
    contract = _contract(delivery_policy="goal_attainment_escalation")
    plan = _completed_plan(digest=canonical_hash(contract))
    assert plan.status == "COMPLETED"

    session = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()

    # create_plan: scalar returns COMPLETED plan
    # request_run: scalar None (new key), get plan, scalar max attempt
    session.scalar = AsyncMock(side_effect=[plan, None, 0])
    session.get = AsyncMock(return_value=plan)
    factory = _session_factory(session)
    svc = GenerationService(factory, MagicMock(), MagicMock())

    reused = await svc.create_plan(
        CreateGenerationPlan(
            requirement_revision_id=plan.requirement_revision_id,
            capability_resolution_plan_id=plan.capability_resolution_plan_id,
            contract=contract,
            architecture_summary="recovery guidance",
            component_plan=[{"name": "app", "type": "web"}],
            actor="regent-core",
            correlation_id="corr",
        )
    )
    assert reused.status == "FROZEN"

    run = await svc.request_run(
        RequestGenerationRun(
            plan_id=reused.id,
            idempotency_key="generation-delivery-recovery:goal:1:evidence:REUSE",
            correlation_id="corr",
        )
    )
    assert run.status == "REQUESTED"
    assert run.attempt == 1
    assert reused.status == "FROZEN"
    session.add.assert_called_once()


def test_reopen_helper_completed_and_failed() -> None:
    plan = _completed_plan()
    GenerationService._reopen_plan_for_run(plan, allow_executing=False)
    assert plan.status == "FROZEN"

    plan.status = "FAILED"
    GenerationService._reopen_plan_for_run(plan, allow_executing=False)
    assert plan.status == "FROZEN"

    plan.status = "EXECUTING"
    with pytest.raises(DomainError):
        GenerationService._reopen_plan_for_run(plan, allow_executing=False)

    GenerationService._reopen_plan_for_run(plan, allow_executing=True)
    assert plan.status == "FROZEN"
