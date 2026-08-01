"""Unit tests for G0 ExternalOperation atomic dispatch."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.external_operation_service import (
    ExternalOperationService,
    request_digest,
)
from regent.application.provider_capability import (
    ProviderCapability,
    require_auto_irreversible,
)
from regent.domain.errors import DomainError
from regent.infrastructure.models import ExecutionPermitModel, ExternalOperationModel


def test_request_digest_is_stable() -> None:
    assert request_digest({"b": 1, "a": 2}) == request_digest({"a": 2, "b": 1})


def test_preview_provider_allows_irreversible() -> None:
    profile = require_auto_irreversible("static-preview-deploy-v1")
    assert ProviderCapability.IDEMPOTENT_REPLAY in profile.capabilities


def test_unknown_provider_fail_closed() -> None:
    with pytest.raises(PermissionError):
        require_auto_irreversible("unregistered-provider")


@pytest.mark.asyncio
async def test_begin_dispatch_consumes_permit_in_same_flow() -> None:
    eo_id = uuid.uuid4()
    permit_id = uuid.uuid4()
    fencing = uuid.uuid4()
    eo = ExternalOperationModel(
        id=eo_id,
        operation_key="op-1",
        provider="static-preview-deploy-v1",
        action="deploy",
        status="PREPARED",
        request_digest="abc",
        permit_id=permit_id,
        local_fencing_token=fencing,
        dispatch_generation=0,
        result_summary={},
    )
    permit = ExecutionPermitModel(
        id=permit_id,
        goal_id=uuid.uuid4(),
        work_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        actor_id="worker",
        action="deploy",
        target="preview",
        parameter_hash="x",
        data_scope={},
        network_scope={},
        resource_limit={},
        risk_level="LOW",
        status="CLAIMED",
        nonce=fencing,
        idempotency_key="k",
        valid_until=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )

    session = AsyncMock()
    session.get = AsyncMock(side_effect=[eo, permit])
    session.flush = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    factory = MagicMock(return_value=session_context)

    result = await ExternalOperationService(factory).begin_dispatch(
        eo_id, worker_lease_token="lease-1", expected_fencing_token=fencing
    )
    assert result.status == "DISPATCHING"
    assert result.dispatch_generation == 1
    assert eo.status == "DISPATCHING"
    assert permit.status == "CONSUMED"


@pytest.mark.asyncio
async def test_begin_dispatch_is_idempotent_when_already_dispatching() -> None:
    eo_id = uuid.uuid4()
    fencing = uuid.uuid4()
    eo = ExternalOperationModel(
        id=eo_id,
        operation_key="op-idem",
        provider="static-preview-deploy-v1",
        action="deploy",
        status="DISPATCHING",
        request_digest="abc",
        permit_id=uuid.uuid4(),
        local_fencing_token=fencing,
        dispatch_generation=1,
        result_summary={},
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=eo)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    factory = MagicMock(return_value=session_context)

    result = await ExternalOperationService(factory).begin_dispatch(
        eo_id, worker_lease_token="lease", expected_fencing_token=fencing
    )
    assert result.dispatch_generation == 1
    assert result.status == "DISPATCHING"


@pytest.mark.asyncio
async def test_prepare_rejects_unclaimed_permit() -> None:
    permit_id = uuid.uuid4()
    fencing = uuid.uuid4()
    permit = ExecutionPermitModel(
        id=permit_id,
        goal_id=uuid.uuid4(),
        work_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        actor_id="worker",
        action="deploy",
        target="preview",
        parameter_hash="x",
        data_scope={},
        network_scope={},
        resource_limit={},
        risk_level="LOW",
        status="APPROVED",
        nonce=fencing,
        idempotency_key="k2",
        valid_until=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.get = AsyncMock(return_value=permit)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    factory = MagicMock(return_value=session_context)

    with pytest.raises(DomainError):
        await ExternalOperationService(factory).prepare(
            operation_key="op-bad",
            provider="static-preview-deploy-v1",
            action="preview-deploy",
            permit_id=permit_id,
            local_fencing_token=fencing,
            payload={"x": 1},
        )


@pytest.mark.asyncio
async def test_mark_failed_terminal_is_idempotent_when_already_failed() -> None:
    eo_id = uuid.uuid4()
    eo = ExternalOperationModel(
        id=eo_id,
        operation_key="op-failed",
        provider="static-preview-deploy-v1",
        action="deploy",
        status="FAILED_TERMINAL",
        request_digest="abc",
        permit_id=uuid.uuid4(),
        local_fencing_token=uuid.uuid4(),
        dispatch_generation=1,
        result_summary={},
    )
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 0
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=eo)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    factory = MagicMock(return_value=session_context)

    await ExternalOperationService(factory).mark_failed_terminal(
        eo_id, failure_code="ALL_CANDIDATES_FAILED"
    )


@pytest.mark.asyncio
async def test_mark_failed_terminal_from_prepared() -> None:
    eo_id = uuid.uuid4()
    eo = ExternalOperationModel(
        id=eo_id,
        operation_key="op-prepared",
        provider="static-preview-deploy-v1",
        action="deploy",
        status="PREPARED",
        request_digest="abc",
        permit_id=uuid.uuid4(),
        local_fencing_token=uuid.uuid4(),
        dispatch_generation=0,
        result_summary={},
    )
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=eo)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    factory = MagicMock(return_value=session_context)

    await ExternalOperationService(factory).mark_failed_terminal(
        eo_id, failure_code="NEVER_DISPATCHED"
    )
    session.execute.assert_awaited()


@pytest.mark.asyncio
async def test_mark_failed_terminal_rejects_wrong_status() -> None:
    eo_id = uuid.uuid4()
    eo = ExternalOperationModel(
        id=eo_id,
        operation_key="op-succeeded",
        provider="static-preview-deploy-v1",
        action="deploy",
        status="SUCCEEDED",
        request_digest="abc",
        permit_id=uuid.uuid4(),
        local_fencing_token=uuid.uuid4(),
        dispatch_generation=1,
        result_summary={},
    )
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 0
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=eo)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    factory = MagicMock(return_value=session_context)

    with pytest.raises(DomainError):
        await ExternalOperationService(factory).mark_failed_terminal(
            eo_id, failure_code="X"
        )
