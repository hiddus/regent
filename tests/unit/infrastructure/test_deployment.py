import pytest
from pydantic import ValidationError
from regent.application.p1_ports import DeploymentRequest, DeploymentResult
from regent.infrastructure.deployment import InMemoryDeploymentProvider


def test_succeeded_deployment_requires_endpoint() -> None:
    with pytest.raises(ValidationError):
        DeploymentResult(external_request_id="id", status="SUCCEEDED")


@pytest.mark.asyncio
async def test_fake_preview_provider_is_idempotent_and_queryable() -> None:
    provider = InMemoryDeploymentProvider()
    request = DeploymentRequest(
        build_artifact_uri="artifact://build",
        environment="preview",
        idempotency_key="deploy-1",
        correlation_id="corr",
    )
    first = await provider.deploy(request)
    second = await provider.deploy(request)
    assert first == second
    assert await provider.query(first.external_request_id) == first
    rolled_back = await provider.rollback(first.external_request_id, "corr")
    assert rolled_back.evidence["rolled_back"] is True
