"""Permit lifecycle behavior tests — revoke, expire, fencing, duplicate claim."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from regent.application.permit_service import PermitBinding, PermitService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import GoalState, RunState, WorkState
from regent.infrastructure.models import (
    ExecutionPermitModel,
    GoalModel,
    RunModel,
    WorkModel,
)


async def _seed_gwr(sessions) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    goal_id, work_id, run_id, corr = (uuid.uuid4() for _ in range(4))
    async with sessions() as session, session.begin():
        session.add_all(
            (
                GoalModel(
                    id=goal_id,
                    original_input="permit behavior fixture",
                    created_by="tester",
                    correlation_id=corr,
                    status=GoalState.ACTIVE.value,
                    metadata_json={},
                ),
                WorkModel(
                    id=work_id,
                    goal_id=goal_id,
                    purpose="permit work",
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


def _binding(
    goal_id: uuid.UUID,
    work_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    key: str,
    valid_until: datetime | None = None,
    risk: str = "LOW",
) -> PermitBinding:
    return PermitBinding(
        goal_id=goal_id,
        work_id=work_id,
        run_id=run_id,
        actor_id="actor-1",
        action="write",
        target="artifact://out",
        parameters={"n": 1},
        data_scope={"paths": ["output/"]},
        network_scope={"hosts": []},
        resource_limit={"cpu": 1},
        risk_level=risk,
        valid_until=valid_until or (datetime.now(UTC) + timedelta(hours=1)),
        idempotency_key=key,
    )


@pytest.mark.permit
@pytest.mark.asyncio
async def test_permit_revoke_blocks_claim(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    svc = PermitService(db_sessions)
    permit_id = await svc.request(_binding(goal_id, work_id, run_id, key="permit-revoke-1"))
    await svc.revoke(permit_id, "safety stop")
    with pytest.raises(DomainError) as raised:
        await svc.claim(permit_id, actor_id="actor-1")
    assert raised.value.code == ErrorCode.INVALID_STATE
    async with db_sessions() as session:
        row = await session.get(ExecutionPermitModel, permit_id)
    assert row is not None and row.status == "REVOKED"


@pytest.mark.permit
@pytest.mark.asyncio
async def test_permit_expire_due(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    svc = PermitService(db_sessions)
    permit_id = await svc.request(
        _binding(
            goal_id,
            work_id,
            run_id,
            key="permit-expire-1",
            valid_until=datetime.now(UTC) - timedelta(seconds=5),
        )
    )
    expired = await svc.expire_due()
    assert expired >= 1
    async with db_sessions() as session:
        row = await session.get(ExecutionPermitModel, permit_id)
    assert row is not None and row.status == "EXPIRED"


@pytest.mark.permit
@pytest.mark.asyncio
async def test_permit_duplicate_claim_rejected(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    svc = PermitService(db_sessions)
    permit_id = await svc.request(_binding(goal_id, work_id, run_id, key="permit-claim-dup"))
    first = await svc.claim(permit_id, actor_id="actor-1")
    assert first.nonce
    with pytest.raises(DomainError) as raised:
        await svc.claim(permit_id, actor_id="actor-1")
    assert raised.value.code == ErrorCode.INVALID_STATE


@pytest.mark.permit
@pytest.mark.asyncio
async def test_permit_fencing_rejects_wrong_nonce(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    svc = PermitService(db_sessions)
    permit_id = await svc.request(_binding(goal_id, work_id, run_id, key="permit-fence-1"))
    claimed = await svc.claim(permit_id, actor_id="actor-1")
    with pytest.raises(DomainError) as raised:
        await svc.consume(permit_id, nonce=uuid.uuid4())
    assert raised.value.code == ErrorCode.INVALID_STATE
    await svc.consume(permit_id, nonce=claimed.nonce)
    async with db_sessions() as session:
        row = await session.get(ExecutionPermitModel, permit_id)
        rows = list(await session.scalars(select(ExecutionPermitModel)))
    assert row is not None and row.status == "CONSUMED"
    assert len(rows) == 1


@pytest.mark.permit
@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_permit_request_idempotent_key(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    svc = PermitService(db_sessions)
    binding = _binding(goal_id, work_id, run_id, key="permit-idem-1")
    a = await svc.request(binding)
    b = await svc.request(binding)
    assert a == b


@pytest.mark.permit
@pytest.mark.asyncio
async def test_delegated_permit_is_narrower_and_bound_to_child(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    svc = PermitService(db_sessions)
    parent = await svc.request(_binding(goal_id, work_id, run_id, key="permit-parent"))
    child = await svc.delegate(
        parent,
        child_actor_id="child-agent",
        data_scope={"paths": ["output/"]},
        network_scope={"hosts": []},
        resource_limit={"cpu": 0.5},
        valid_until=datetime.now(UTC) + timedelta(minutes=10),
        idempotency_key="permit-child",
    )
    claimed = await svc.claim(child, actor_id="child-agent")
    assert claimed.binding.actor_id == "child-agent"
    assert claimed.binding.resource_limit == {"cpu": 0.5}


@pytest.mark.permit
@pytest.mark.asyncio
async def test_delegated_permit_cannot_widen_parent_scope(db_sessions) -> None:
    goal_id, work_id, run_id = await _seed_gwr(db_sessions)
    svc = PermitService(db_sessions)
    parent = await svc.request(_binding(goal_id, work_id, run_id, key="permit-parent-wide"))
    with pytest.raises(DomainError) as raised:
        await svc.delegate(
            parent,
            child_actor_id="child-agent",
            data_scope={"paths": ["output/", "secrets/"]},
            network_scope={"hosts": []},
            resource_limit={"cpu": 2},
            valid_until=datetime.now(UTC) + timedelta(minutes=10),
            idempotency_key="permit-child-wide",
        )
    assert raised.value.code == ErrorCode.PERMIT_INVALID
