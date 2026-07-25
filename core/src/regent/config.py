from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REGENT_", env_file=".env", extra="ignore")
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://regent:regent@localhost:5432/regent"
    log_level: str = "INFO"
    artifact_root: str = "/var/lib/regent/artifacts"
    workspace_root: str = "/var/lib/regent/workspaces"
    build_root: str = "/var/lib/regent/builds"
    sandbox_image: str = "regent-python-web-v1-sandbox:1"
    sandbox_mode: Literal["docker", "local"] = "local"
    dependency_resolver_image: str = "regent-python-web-v1-resolver:1"
    dependency_egress_proxy: str | None = None
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=30, ge=5)
    model_provider: str = "openai-compatible"
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    observation_signing_key: SecretStr | None = None
    experiment_signing_key: SecretStr | None = None
    public_base_url: str | None = None
    evidence_egress_proxy: str | None = None
    # Operator platform safety allowlist (hosts Core may fetch IF Goal authorizes URLs).
    # Empty = HTTP evidence connector fetches nothing. Not a product feed catalog.
    evidence_allowed_domains: str = ""
    evidence_max_bytes: int = Field(default=262_144, ge=1024)
    # Comma-separated org keys for worker scheduler ticks; empty = discover from queue.
    scheduler_org_keys: str = ""
    scheduler_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
