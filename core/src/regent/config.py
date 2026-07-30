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
    # GQ-3/GQ-4 hooks: kill switch forces fallback for NEW runs; in-flight keep frozen plan.
    generation_strategy_kill_switch: bool = False
    generation_strategy_fallback: Literal["artifact-backed", "agentic"] = "artifact-backed"
    # Canary percent 0..100; diagnosis order requires GQ-2 closed before enabling (>0).
    generation_strategy_canary_percent: int = Field(default=0, ge=0, le=100)
    generation_strategy_canary_variant: Literal["artifact-backed", "agentic"] = "agentic"
    # Shadow tasks: forbid publish / external side effects (contract flag for runners).
    generation_strategy_shadow_mode: bool = False
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
    # Opt-in certified fixed hive template (pm-dev-independent-qa-v1) when feasible.
    # Default False keeps single-agent champion. Does NOT enable adaptive free topology.
    aar1_certified_hive: bool = False
    # When True, ReleaseCandidate approve requires a completed human APPROVE task.
    require_release_human_approval: bool = True
    # Worker reconciliation sweep interval (seconds) for stale ExternalOperations.
    reconciliation_interval_seconds: float = Field(default=300.0, ge=30.0)
    # PRD §7.1–7.3 privacy: consent gate + retention anonymization.
    # When True, Observation/Evidence/conversation collection requires GRANTED consent.
    privacy_consent_enforced: bool = True
    # Default retention for Observations / operational payloads (days); overdue → anonymize.
    privacy_retention_days: int = Field(default=90, ge=1, le=3650)
    # Worker retention sweep interval (seconds).
    privacy_retention_interval_seconds: float = Field(default=3600.0, ge=60.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
