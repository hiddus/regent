from fastapi.testclient import TestClient
from regent.api.main import create_app


def test_liveness() -> None:
    response = TestClient(create_app()).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_exposes_operations_health() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()
    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
