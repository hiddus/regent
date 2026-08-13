import pytest

from regent.config import Settings, effective_runtime_profile


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.sandbox_mode == "docker"
    assert settings.worker_lease_seconds >= 5


def test_effective_runtime_profile_has_no_secrets() -> None:
    settings = Settings(_env_file=None, model_api_key="secret")
    profile = effective_runtime_profile(settings)
    assert profile["generation_strategy"] == "agentic"
    assert "model_api_key" not in profile


def test_production_requires_nonzero_model_price_book() -> None:
    with pytest.raises(ValueError, match="non-zero model input/output prices"):
        Settings(_env_file=None, environment="production", sandbox_mode="docker")

    settings = Settings(
        _env_file=None,
        environment="production",
        sandbox_mode="docker",
        model_input_cost_per_million=1.0,
        model_output_cost_per_million=2.0,
    )
    assert effective_runtime_profile(settings)["model_pricing_configured"] is True
