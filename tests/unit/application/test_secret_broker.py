import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from regent.application.permit_service import ClaimedPermit, PermitBinding
from regent.application.secret_broker import (
    BrokerReceipt,
    BrokerRequest,
    IsolatedSecretBroker,
)
from regent.domain.errors import DomainError


def permit() -> ClaimedPermit:
    binding = PermitBinding(
        goal_id=uuid.uuid4(),
        work_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        actor_id="agent",
        action="deploy",
        target="preview",
        parameters={},
        data_scope={},
        network_scope={},
        resource_limit={},
        risk_level="HIGH",
        valid_until=datetime.now(UTC) + timedelta(minutes=1),
        idempotency_key="operation-123",
    )
    return ClaimedPermit(id=uuid.uuid4(), nonce=uuid.uuid4(), binding=binding)


async def test_secret_broker_keeps_credential_inside_privileged_executor() -> None:
    executor = AsyncMock()
    executor.execute.return_value = BrokerReceipt("SUCCEEDED", "request-1", {"status": 200})
    broker = IsolatedSecretBroker({"deploy-key": SecretStr("hidden")}, executor)
    request = BrokerRequest("deploy", "preview", {"version": "1"}, "deploy-key")

    receipt = await broker.execute(permit(), request)

    assert receipt.outcome == "SUCCEEDED"
    assert "hidden" not in str(receipt)
    credential = executor.execute.await_args.kwargs["credential"]
    assert credential.get_secret_value() == "hidden"


async def test_secret_broker_rejects_binding_mismatch() -> None:
    broker = IsolatedSecretBroker({"deploy-key": SecretStr("hidden")}, AsyncMock())
    request = BrokerRequest("delete", "preview", {}, "deploy-key")
    with pytest.raises(DomainError, match="does not match"):
        await broker.execute(permit(), request)


async def test_secret_broker_rejects_leaked_secret() -> None:
    executor = AsyncMock()
    executor.execute.return_value = BrokerReceipt("SUCCEEDED", "request-1", {"token": "hidden"})
    broker = IsolatedSecretBroker({"deploy-key": SecretStr("hidden")}, executor)
    request = BrokerRequest("deploy", "preview", {}, "deploy-key")
    with pytest.raises(RuntimeError, match="leaked"):
        await broker.execute(permit(), request)
