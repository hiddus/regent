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
    # Generation strategy used by the installed worker image (optional locally).
    generation_strategy: Literal["artifact-backed", "agentic"] = "artifact-backed"
    delivery_batch_enabled: bool = False
    agent_max_turns: int = Field(default=40, ge=1, le=200)
    agent_max_tokens: int = Field(default=200_000, ge=1_000)
    agent_max_wall_seconds: int = Field(default=900, ge=30)
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
    search_api_url: str | None = None
    search_api_key: SecretStr | None = None
    vercel_token: SecretStr | None = None
    vercel_team_id: str | None = None
    netlify_token: SecretStr | None = None
    tunnel_type: str = "cloudflared"
    # AAR-1 Foundation phase (Expand->Dual-write->Read-switch->Enforce->Contract).
    # ROLLOUT_NOT_ALLOWED: adaptive multi-agent must never become default via this flag.
    aar1_phase: Literal[
        "expand", "dual_write", "read_switch", "enforce", "contract"
    ] = "contract"
    aar1_envelope_hmac_key: SecretStr | None = None
    aar1_shadow_log_divergences: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
