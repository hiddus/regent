"""Shared FileChangeSetGenerator factory (GQ-1 Worker + API parity)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.agent.generator import AgenticCodeGenerator
from regent.application.generator_metadata import (
    GenerationStrategy,
    assert_generator_consistency,
    metadata_for_strategy,
)
from regent.application.generation_strategy_policy import resolve_effective_generation_strategy
from regent.application.delivery_state import resolve_delivery_budget, resolve_delivery_persona
from regent.config import Settings
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.code_generator import ArtifactBackedCodeGenerator
from regent.model import ModelProvider


def build_code_generator(
    settings: Settings,
    provider: ModelProvider,
    artifacts: FileArtifactStore,
    *,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    goal_id: str | None = None,
    enforce_consistency: bool = True,
) -> Any:
    """Construct the generator implied by effective generation_strategy.

    Applies kill-switch / canary resolution first. When ``enforce_consistency``
    is True (default), immediately fail-closes if constructed object metadata
    disagrees with the resolved strategy.
    """
    strategy: GenerationStrategy = resolve_effective_generation_strategy(
        settings, goal_id=goal_id
    )
    if strategy == "agentic":
        generator: Any = AgenticCodeGenerator(
            provider,
            artifacts,
            workspace_root=Path(settings.workspace_root),
            budget=resolve_delivery_budget(
                resolve_delivery_persona(settings.delivery_profile),
                settings.agent_max_turns,
                settings.agent_max_tokens,
                settings.agent_max_wall_seconds,
            ),
            sessions=sessions,
        )
    else:
        generator = ArtifactBackedCodeGenerator(
            provider,
            artifacts,
            semantic_alignment_enabled=settings.goal_semantic_alignment_enabled,
            workspace_root=Path(settings.workspace_root),
        )

    if enforce_consistency:
        assert_generator_consistency(strategy=strategy, generator=generator)
    return generator


def plan_metadata_for_settings(
    settings: Settings, *, goal_id: str | None = None
) -> dict[str, str]:
    strategy = resolve_effective_generation_strategy(settings, goal_id=goal_id)
    return metadata_for_strategy(strategy)


class GeneratorSelector:
    """Per-goal generator selection with lazy agentic construction.

    Eagerly holds the lightweight artifact-backed generator; constructs
    ``AgenticCodeGenerator`` keyed by delivery budget (CD-7.4) so a frozen
    first-call budget cannot leak across goals / profile changes.
    """

    def __init__(
        self,
        *,
        artifact_backed: Any,
        settings: Settings,
        provider: ModelProvider,
        artifacts: FileArtifactStore,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        enforce_consistency: bool = True,
    ) -> None:
        self._artifact_backed = artifact_backed
        self._agentic_by_budget: dict[tuple[Any, ...], Any] = {}
        self._settings = settings
        self._provider = provider
        self._artifacts = artifacts
        self._sessions = sessions
        self._enforce_consistency = enforce_consistency

    def select(self, goal_id: str | None = None) -> Any:
        strategy = resolve_effective_generation_strategy(self._settings, goal_id=goal_id)
        if strategy == "agentic":
            return self._ensure_agentic()
        return self._artifact_backed

    def _ensure_agentic(self) -> Any:
        persona = resolve_delivery_persona(self._settings.delivery_profile)
        budget = resolve_delivery_budget(
            persona,
            self._settings.agent_max_turns,
            self._settings.agent_max_tokens,
            self._settings.agent_max_wall_seconds,
        )
        key = (
            persona.value,
            getattr(budget, "max_turns", None),
            getattr(budget, "max_tokens", None),
            getattr(budget, "max_wall_seconds", None),
        )
        cached = self._agentic_by_budget.get(key)
        if cached is not None:
            return cached
        agentic = AgenticCodeGenerator(
            self._provider,
            self._artifacts,
            workspace_root=Path(self._settings.workspace_root),
            budget=budget,
            sessions=self._sessions,
        )
        if self._enforce_consistency:
            assert_generator_consistency(strategy="agentic", generator=agentic)
        self._agentic_by_budget[key] = agentic
        return agentic


def build_generator_selector(
    settings: Settings,
    provider: ModelProvider,
    artifacts: FileArtifactStore,
    *,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    enforce_consistency: bool = True,
) -> Any:
    """Build a per-goal ``GeneratorSelector`` (artifact-backed eager; agentic lazy).

    Returns ``None`` when no model provider is configured (matches the prior
    single-generator behaviour where generation is skipped).
    """
    if provider is None:
        return None
    artifact_backed = ArtifactBackedCodeGenerator(
        provider,
        artifacts,
        semantic_alignment_enabled=settings.goal_semantic_alignment_enabled,
        workspace_root=Path(settings.workspace_root),
    )
    if enforce_consistency:
        assert_generator_consistency(strategy="artifact-backed", generator=artifact_backed)
    return GeneratorSelector(
        artifact_backed=artifact_backed,
        settings=settings,
        provider=provider,
        artifacts=artifacts,
        sessions=sessions,
        enforce_consistency=enforce_consistency,
    )
