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
    # Safe default for every real workspace. Tests may explicitly choose local.
    sandbox_mode: Literal["docker", "local"] = "docker"
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
    # Optional secondary/tertiary models (gateway aliases); primary is model_name.
    model_name_2: str | None = None
    model_name_3: str | None = None
    model_api_key: SecretStr | None = None
    # GLM / long codegen often exceeds 180s; 504s still retry via outbox backoff.
    model_timeout_seconds: float = Field(default=300.0, ge=30.0, le=1800.0)
    # M1-1: configurable chat completion output cap (None disables max_tokens).
    model_max_output_tokens: int | None = Field(default=8192, ge=256, le=128_000)
    model_input_cost_per_million: float = Field(default=0.0, ge=0.0)
    model_output_cost_per_million: float = Field(default=0.0, ge=0.0)
    model_price_book_version: str = "model-price-book-v1"
    # DeepSeek V4 enables thinking by default; CoT shares max_tokens with content/tools.
    # Agent tool loops default to disabled to avoid empty finish_reason=length.
    model_thinking_mode: Literal["disabled", "enabled", "default"] = "disabled"
    # Ship-first default: always agentic. artifact-backed is kill-switch fallback only.
    generation_strategy: Literal["artifact-backed", "agentic"] = "agentic"
    generation_strategy_kill_switch: bool = False
    generation_strategy_fallback: Literal["artifact-backed", "agentic"] = "artifact-backed"
    # Peer AB↔agentic canary is dead (M3). Retained for ops telemetry only;
    # resolve_effective_generation_strategy ignores percent for arm selection.
    generation_strategy_canary_percent: int = Field(default=0, ge=0, le=100)
    generation_strategy_canary_variant: Literal["artifact-backed", "agentic"] = "agentic"
    generation_strategy_canary_gate: bool = False
    # Ops reporting ladder only — does NOT demote product path to artifact-backed.
    # artifact-backed remains SCAFFOLD_OR_KILL_SWITCH_FALLBACK (see generation_strategy_policy).
    agentic_qualification_state: Literal[
        "DISABLED",
        "OFFLINE_QUALIFICATION",
        "INTERNAL_DOGFOOD",
        "CANARY_5",
        "CANARY_25",
        "CANARY_50",
        "DEFAULT",
    ] = "DISABLED"
    generation_strategy_shadow_mode: bool = False
    delivery_batch_enabled: bool = False
    delivery_profile: Literal["aggressive", "balanced", "conservative"] = "balanced"
    agent_max_turns: int = Field(default=40, ge=1, le=200)
    agent_max_tokens: int = Field(default=400_000, ge=1_000)
    agent_max_wall_seconds: int = Field(default=900, ge=30)
    agent_nested_repair_max: int = Field(default=4, ge=0, le=8)
    # M1: VerificationGap prefers Session chassis; A0 forbids silent auto-resume.
    # When agent_loop_exit_enforced, gap → ASK_HUMAN/STOP; resume only after human.
    agent_session_resume_enabled: bool = True
    agent_loop_exit_enforced: bool = True
    # Progress ROI: each spend cycle must buy measurable delivery progress.
    # Empty continue_fix is upgraded to self_repair → replan_global → STOP.
    progress_roi_enforced: bool = True
    progress_roi_min_tokens: int = Field(default=2000, ge=0)
    progress_roi_stagnant_stop: int = Field(default=3, ge=1, le=10)
    # Session Work Plan (W1–W2): force Step 0 checklist before write tools.
    agent_work_plan_required: bool = True
    # First substantial plan in a Session may ASK plan_approve (OpenWork-style).
    agent_plan_approve_on_first: bool = True
    # soft: product HTML/tests/smoke do not hard-reject; only empty + BLOCKING_* fail.
    # full: legacy hard gates (death-loop risk). off: skip product review (still empty/safety).
    # A0: soft must NOT map to exit_kind COMPLETE with stop_reason=verified_pass.
    delivery_product_gates_mode: Literal["full", "soft", "off"] = "soft"
    # W4-P1-4: follow model context window (default matches common 128k).
    agent_context_window_tokens: int = Field(default=128_000, ge=8_000, le=2_000_000)
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
    # Product default: prefer certified multi-agent Hive (pm-dev-independent-qa-v1).
    # Opt out per-goal via metadata force_single_agent / hive_enabled=false, or set env false.
    aar1_certified_hive: bool = True
    # Nested delegate_plan_item depth (0 = no delegation).
    # Default 1: only the main Agent may delegate; sub-agents cannot re-delegate.
    # This prevents A→B→A dead-loops and wasteful token burn.
    max_subagent_depth: int = Field(default=1, ge=0, le=8)
    require_release_human_approval: bool = True
    decision_preference: Literal["aggressive", "balanced", "conservative"] = "balanced"
    decision_allow_actions: str = ""
    decision_deny_actions: str = ""
    confirmation_timeout_seconds: int = Field(default=300, ge=0)
    reconciliation_interval_seconds: float = Field(default=300.0, ge=30.0)
    # Host self-heal: measure disk/mem/load, prune preview venvs, soft-pause burn.
    host_guard_enabled: bool = True
    host_guard_interval_seconds: float = Field(default=60.0, ge=15.0)
    host_disk_percent_max: float = Field(default=85.0, ge=50.0, le=99.0)
    host_mem_percent_max: float = Field(default=92.0, ge=50.0, le=99.5)
    host_load1_per_cpu_max: float = Field(default=4.0, ge=1.0, le=32.0)
    host_prune_disk_percent: float = Field(default=80.0, ge=50.0, le=99.0)
    host_prune_mem_percent: float = Field(default=85.0, ge=50.0, le=99.5)
    host_prune_preview_keep: int = Field(default=8, ge=1, le=64)
    host_reap_preview_processes: bool = True
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
        if self.environment == "production" and (
            self.model_input_cost_per_million <= 0
            or self.model_output_cost_per_million <= 0
        ):
            raise ValueError(
                "production requires non-zero model input/output prices so "
                "pre-dispatch budget reservations cannot silently reserve zero"
            )
        # N-1: previous canary∩!docker check was unreachable (subset of the rule above).
        # Canary still requires docker via the production sandbox invariant.
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def effective_runtime_profile(settings: Settings) -> dict[str, object]:
    """Non-secret runtime facts suitable for health, audit and support."""
    return {
        "environment": settings.environment,
        "sandbox_mode": settings.sandbox_mode,
        "generation_strategy": settings.generation_strategy,
        "generation_kill_switch": settings.generation_strategy_kill_switch,
        "canary_gate": settings.generation_strategy_canary_gate,
        "canary_percent": settings.generation_strategy_canary_percent,
        "certified_hive": settings.aar1_certified_hive,
        "max_turns": settings.agent_max_turns,
        "max_tokens": settings.agent_max_tokens,
        "max_wall_seconds": settings.agent_max_wall_seconds,
        "max_subagent_depth": settings.max_subagent_depth,
        "model_price_book_version": settings.model_price_book_version,
        "model_pricing_configured": (
            settings.model_input_cost_per_million > 0
            and settings.model_output_cost_per_million > 0
        ),
    }
