"""Recovery / UNKNOWN reconciliation / lease reclaim behavior tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from regent.application.agent_task_service import recover_after_crash
from regent.application.external_operation_service import ExternalOperationService
from regent.application.permit_service import PermitBinding, PermitService
from regent.application.reconciliation_worker import ReconciliationWorker
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import GoalState, RunState, WorkState
from regent.infrastructure.models import (
    ExternalOperationModel,
    GoalModel,
    RunModel,
    WorkModel,
    WorkerLeaseModel,
)
from regent.runtime.worker_leases import WorkerLeaseService


async def _seed_gwr(sessions) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    goal_id, work_id, run_id, corr = (uuid.uuid4() for _ in range(4))
    async with sessions() as session, session.begin():
        session.add_all(
            (
                GoalModel(
                    id=goal_id,
                    original_input="recovery fixture",
                    created_by="tester",
                    correlation_id=corr,
                    status=GoalState.ACTIVE.value,
                    metadata_json={},
                ),
                WorkModel(
                    id=work_id,
                    goal_id=goal_id,
                    purpose="recovery work",
                    input_refs=[],
                    acceptance_criteria={},
                    dependency_ids=[],
                    priority=0,
                    budget={},
                    status=WorkState.RUNNING.value,
                    correlation_id=corr,
                    metadata_json={},
                ),
                RunModel(
                    id=run_id,
                    work_id=work_id,
                    actor_id="actor-1",
                    tool_ref="tool:v1",
                    input_version="sha256:x",
                    idempotency_key=f"run-{run_id}",
                    resource_usage={},
                    status=RunState.RUNNING.value,
                    correlation_id=corr,
                ),
            )
        )
    return goal_id, work_id, run_id


@pytest.mark.recovery
def test_worker_crash_recover_after_crash_paths() -> None:
    assert recover_after_crash("OFFERED", dispatched=False) == "OFFERED"
    assert recover_after_crash("RUNNING", dispatched=False) == "FAILED_RETRYABLE"
    assert recover_after_crash("RUNNING", dispatched=True) == "UNKNOWN"
    assert recover_after_crash("ACCEPTED", dispatched=True) == "UNKNOWN"


@pytest.mark.recovery
@pytest.mark.asyncio
async def test_worker_lease_reclaim_after_expiry(db_sessions) -> None:
    leases = WorkerLeaseService(db_sessions, lease_seconds=30)
    first = await leases.acquire("worker-recovery-1", metadata={"host": "a"})
    # Force expiry so a crash recovery can reclaim the same worker_id.
    async with db_sessions() as session, session.begin():
        from sqlalchemy import func, select

        db_now = await session.scalar(select(func.now()))
        assert db_now is not None
        row = await session.get(WorkerLeaseModel, "worker-recovery-1")
        assert row is not None
        row.expires_at = db_now - timedelta(seconds=1)
    reclaimed = await leases.acquire("worker-recovery-1", metadata={"host": "b"})
    assert reclaimed.worker_id == first.worker_id
    assert reclaimed.token != first.token


@pytest.mark.recovery
@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_unknown_reconciliation_timeout_path(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    permits = PermitService(db_sessions)
    permit_id = await permits.request(
        PermitBinding(
            goal_id=goal_id,
            work_id=work_id,
            run_id=run_id,
            actor_id="actor-1",
            action="http.post",
            target="https://example.test/hook",
            parameters={},
            data_scope={},
            network_scope={"hosts": ["example.test"]},
            resource_limit={},
            risk_level="LOW",
            valid_until=datetime.now(UTC) + timedelta(hours=1),
            idempotency_key=f"eo-permit-{run_id}",
        )
    )
    claimed = await permits.claim(permit_id, actor_id="actor-1")
    eos = ExternalOperationService(db_sessions)
    prepared = await eos.prepare(
        operation_key=f"op-{run_id}",
        provider="allowlisted-http-source-v1",
        action="fetch",
        permit_id=permit_id,
        local_fencing_token=claimed.nonce,
        payload={"url": "https://example.test/hook"},
        goal_id=goal_id,
    )
    await eos.begin_dispatch(
        prepared.id,
        expected_fencing_token=claimed.nonce,
        worker_lease_token="lease-1",
    )
    await eos.mark_unknown(prepared.id, reason="response_lost")

    # Stale clock: updated_at must be older than 15 minutes.
    stale_at = datetime.now(UTC) - timedelta(minutes=20)
    async with db_sessions() as session, session.begin():
        eo = await session.get(ExternalOperationModel, prepared.id)
        assert eo is not None
        eo.updated_at = stale_at
        eo.status = "UNKNOWN"

    worker = ReconciliationWorker(db_sessions, timeout_minutes=15)
    reconciled = await worker.tick(now=datetime.now(UTC))
    assert prepared.id in reconciled

    async with db_sessions() as session:
        eo = await session.get(ExternalOperationModel, prepared.id)
    assert eo is not None and eo.status == "RECONCILING"


@pytest.mark.recovery
@pytest.mark.asyncio
async def test_fencing_mismatch_blocks_prepare(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    permits = PermitService(db_sessions)
    permit_id = await permits.request(
        PermitBinding(
            goal_id=goal_id,
            work_id=work_id,
            run_id=run_id,
            actor_id="actor-1",
            action="http.post",
            target="https://example.test/hook",
            parameters={},
            data_scope={},
            network_scope={},
            resource_limit={},
            risk_level="LOW",
            valid_until=datetime.now(UTC) + timedelta(hours=1),
            idempotency_key=f"eo-fence-{run_id}",
        )
    )
    await permits.claim(permit_id, actor_id="actor-1")
    eos = ExternalOperationService(db_sessions)
    with pytest.raises(DomainError) as raised:
        await eos.prepare(
            operation_key=f"op-fence-{run_id}",
            provider="allowlisted-http-source-v1",
            action="fetch",
            permit_id=permit_id,
            local_fencing_token=uuid.uuid4(),
            payload={"url": "https://example.test/hook"},
            goal_id=goal_id,
        )
    assert raised.value.code == ErrorCode.INVALID_STATE
