from regent.config import Settings


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.worker_lease_seconds >= 5
