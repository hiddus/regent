"""DeploymentSmokeTestService unit tests."""


from regent.application.smoke_test_service import (
    DeploymentSmokeTestService,
    SmokeTestResult,
)


def test_smoke_test_result_dataclass() -> None:
    """SmokeTestResult can be created."""
    result = SmokeTestResult(
        passed=True,
        endpoint="http://localhost:8080",
        checks=[{"check": "endpoint_configured", "passed": True}],
        errors=[],
    )
    assert result.passed is True
    assert result.endpoint == "http://localhost:8080"
    assert len(result.checks) == 1
    assert len(result.errors) == 0


def test_smoke_test_service_can_be_created() -> None:
    """DeploymentSmokeTestService can be instantiated."""
    service = DeploymentSmokeTestService(sessions=None)
    assert service is not None


def test_smoke_test_service_has_run_smoke_test() -> None:
    """DeploymentSmokeTestService has run_smoke_test method."""
    service = DeploymentSmokeTestService(sessions=None)
    assert hasattr(service, "run_smoke_test")
    assert callable(service.run_smoke_test)
