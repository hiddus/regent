from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
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
    agent_sandbox_image: str = "regent-agent-exec-v1:1"
    sandbox_mode: Literal["docker", "local"] = "local"
    # CD-6.3: container_prefix=host_prefix;... e.g. /var/lib/regent=/opt/regent
    host_path_map: str = ""
    # CD-6.2: override sandbox --user (uid or uid:gid); empty = os.getuid()/getgid()
    agent_sandbox_uid: str | None = None
    dependency_resolver_image: str = "regent-python-web-v1-resolver:1"
    dependency_egress_proxy: str | None = None
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=30, ge=5)
    model_provider: str = "openai-compatible"
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    # GLM / long codegen often exceeds 180s; 504s still retry via outbox backoff.
    model_timeout_seconds: float = Field(default=300.0, ge=30.0, le=1800.0)
    # M1-1: configurable chat completion output cap (None disables max_tokens).
    model_max_output_tokens: int | None = Field(default=8192, ge=256, le=128_000)
    generation_strategy: Literal["artifact-backed", "agentic"] = "artifact-backed"
    generation_strategy_kill_switch: bool = False
    generation_strategy_fallback: Literal["artifact-backed", "agentic"] = "artifact-backed"
    generation_strategy_canary_percent: int = Field(default=0, ge=0, le=100)
    generation_strategy_canary_variant: Literal["artifact-backed", "agentic"] = "agentic"
    generation_strategy_canary_gate: bool = False
    generation_strategy_shadow_mode: bool = False
    delivery_batch_enabled: bool = False
    delivery_profile: Literal["aggressive", "balanced", "conservative"] = "balanced"
    agent_max_turns: int = Field(default=40, ge=1, le=200)
    agent_max_tokens: int = Field(default=200_000, ge=1_000)
    agent_max_wall_seconds: int = Field(default=900, ge=30)
    agent_nested_repair_max: int = Field(default=2, ge=0, le=8)
    goal_semantic_alignment_enabled: bool = False
    observation_signing_key: SecretStr | None = None
    experiment_signing_key: SecretStr | None = None
    public_base_url: str | None = None
    evidence_egress_proxy: str | None = None
    evidence_allowed_domains: str = ""
    evidence_max_bytes: int = Field(default=262_144, ge=1024)
    scheduler_org_keys: str = ""
    scheduler_enabled: bool = True
    search_api_url: str | None = None
    search_api_key: SecretStr | None = None
    vercel_token: SecretStr | None = None
    vercel_team_id: str | None = None
    netlify_token: SecretStr | None = None
    tunnel_type: str = "cloudflared"
    aar1_phase: Literal[
        "expand", "dual_write", "read_switch", "enforce", "contract"
    ] = "contract"
    aar1_envelope_hmac_key: SecretStr | None = None
    aar1_shadow_log_divergences: bool = True
    aar1_certified_hive: bool = False
    require_release_human_approval: bool = True
    decision_preference: Literal["aggressive", "balanced", "conservative"] = "balanced"
    decision_allow_actions: str = ""
    decision_deny_actions: str = ""
    confirmation_timeout_seconds: int = Field(default=300, ge=0)
    reconciliation_interval_seconds: float = Field(default=300.0, ge=30.0)
    # 0 = derive from worker_replicas × worker_dispatch_concurrency × 2
    max_concurrent_generating: int = Field(default=0, ge=0, le=64)
    worker_dispatch_concurrency: int = Field(default=2, ge=1, le=32)
    worker_replicas: int = Field(default=1, ge=1, le=32)
    privacy_consent_enforced: bool = True
    privacy_retention_days: int = Field(default=90, ge=1, le=3650)
    privacy_retention_interval_seconds: float = Field(default=3600.0, ge=60.0)

    @model_validator(mode="after")
    def _enforce_production_sandbox(self) -> Self:
        if self.environment == "production" and self.sandbox_mode != "docker":
            raise ValueError(
                "sandbox_mode must be 'docker' when environment=production "
                "(Tech-Spec §13.8 / CD-0.1)"
            )
        # N-1: previous canary∩!docker check was unreachable (subset of the rule above).
        # Canary still requires docker via the production sandbox invariant.
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
