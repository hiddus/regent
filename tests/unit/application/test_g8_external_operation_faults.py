"""G8-oriented fault injection checks for ExternalOperation + ReleaseService wiring."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.external_operation_service import (
    DispatchingExternalOperation,
    PreparedExternalOperation,
)
from regent.application.p1_ports import DeploymentResult
from regent.application.permit_service import ClaimedPermit, PermitBinding
from regent.application.release_service import ReleaseService
from regent.infrastructure.models import DeploymentModel


def _permit() -> ClaimedPermit:
    nonce = uuid.uuid4()
    return ClaimedPermit(
        id=uuid.uuid4(),
        nonce=nonce,
        binding=PermitBinding(
            goal_id=uuid.uuid4(),
            work_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            actor_id="preview-deployment-provider",
            action="preview-deploy",
            target="x",
            parameters={},
            data_scope={},
            network_scope={},
            resource_limit={},
            risk_level="LOW",
            valid_until=__import__("datetime").datetime.now(__import__("datetime").UTC),
            idempotency_key="perm-1",
        ),
    )


def _sessions_factory(existing: DeploymentModel | None = None) -> MagicMock:
    session = AsyncMock()
    session.get = AsyncMock(return_value=existing)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    return MagicMock(return_value=session_context)


@pytest.mark.asyncio
async def test_execute_marks_unknown_on_provider_crash_after_dispatch() -> None:
    deployment_id = uuid.uuid4()
    deployment = DeploymentModel(
        id=deployment_id,
        release_candidate_id=uuid.uuid4(),
        permit_id=uuid.uuid4(),
        environment="preview",
        status="DEPLOYING",
        version=1,
        idempotency_key="deploy-key-1",
        evidence={},
        reconciliation_required=False,
        correlation_id="corr",
    )
    permit = _permit()
    prepared = PreparedExternalOperation(
        uuid.uuid4(), "preview-deploy:deploy-key-1", permit.nonce, "PREPARED"
    )
    provider = AsyncMock()
    provider.deploy = AsyncMock(side_effect=TimeoutError("lost response"))

    service = ReleaseService(_sessions_factory(None), provider)
    service._claim = AsyncMock(return_value=(deployment, "artifact://x"))  # type: ignore[method-assign]
    service._mark_unknown = AsyncMock()  # type: ignore[method-assign]
    service._permits.claim = AsyncMock(return_value=permit)
    service._external_ops.get_by_operation_key = AsyncMock(return_value=None)
    service._external_ops.prepare = AsyncMock(return_value=prepared)
    service._external_ops.begin_dispatch = AsyncMock(
        return_value=DispatchingExternalOperation(
            prepared.id, prepared.operation_key, 1, permit.nonce, "DISPATCHING"
        )
    )
    service._external_ops.mark_unknown = AsyncMock()

    with pytest.raises(TimeoutError):
        await service.execute(deployment_id)

    service._external_ops.begin_dispatch.assert_awaited()
    service._external_ops.mark_unknown.assert_awaited_with(
        prepared.id, reason="provider_exception"
    )
    service._mark_unknown.assert_awaited_with(deployment_id)


@pytest.mark.asyncio
async def test_execute_resumes_same_operation_key_when_already_dispatching() -> None:
    deployment_id = uuid.uuid4()
    deployment = DeploymentModel(
        id=deployment_id,
        release_candidate_id=uuid.uuid4(),
        permit_id=uuid.uuid4(),
        environment="preview",
        status="DEPLOYING",
        version=1,
        idempotency_key="deploy-key-2",
        evidence={},
        reconciliation_required=False,
        correlation_id="corr",
    )
    eo_id = uuid.uuid4()
    existing_eo = MagicMock()
    existing_eo.id = eo_id
    existing_eo.status = "DISPATCHING"
    existing_eo.external_id = None

    provider = AsyncMock()
    provider.deploy = AsyncMock(
        return_value=DeploymentResult(
            status="SUCCEEDED",
            external_request_id="ext-1",
            endpoint="http://preview/x",
            evidence={"ok": True},
        )
    )

    service = ReleaseService(_sessions_factory(None), provider)
    service._claim = AsyncMock(return_value=(deployment, "artifact://x"))  # type: ignore[method-assign]
    service._commit_result = AsyncMock(return_value=deployment)  # type: ignore[method-assign]
    service._permits.claim = AsyncMock()
    service._external_ops.get_by_operation_key = AsyncMock(return_value=existing_eo)
    service._external_ops.prepare = AsyncMock()
    service._external_ops.begin_dispatch = AsyncMock()
    service._external_ops.mark_succeeded = AsyncMock()

    await service.execute(deployment_id)

    service._permits.claim.assert_not_awaited()
    service._external_ops.prepare.assert_not_awaited()
    service._external_ops.mark_succeeded.assert_awaited()
    provider.deploy.assert_awaited_once()
