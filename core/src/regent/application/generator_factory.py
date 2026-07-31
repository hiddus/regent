"""Shared FileChangeSetGenerator factory (GQ-1 Worker + API parity)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.agent.generator import AgenticCodeGenerator
from regent.agent.types import AgentBudget
from regent.application.generator_metadata import (
    GenerationStrategy,
    assert_generator_consistency,
    metadata_for_strategy,
)
from regent.application.generation_strategy_policy import resolve_effective_generation_strategy
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
            budget=AgentBudget(
                max_turns=settings.agent_max_turns,
                max_tokens=settings.agent_max_tokens,
                max_wall_seconds=settings.agent_max_wall_seconds,
            ),
            sessions=sessions,
        )
    else:
        generator = ArtifactBackedCodeGenerator(provider, artifacts)

    if enforce_consistency:
        assert_generator_consistency(strategy=strategy, generator=generator)
    return generator


def plan_metadata_for_settings(
    settings: Settings, *, goal_id: str | None = None
) -> dict[str, str]:
    strategy = resolve_effective_generation_strategy(settings, goal_id=goal_id)
    return metadata_for_strategy(strategy)


class GeneratorSelector:
    """Holds both strategy generators; selects the one matching the resolved
    effective strategy for a concrete goal.

    The single injected ``self._generator`` historically capped canary at the
    startup default (artifact-backed), so a canary-resolved ``agentic`` goal
    would fail-closed on a metadata mismatch. The selector lets the runtime
    honour the per-goal decision instead of forcing one singleton.
    """

    def __init__(
        self,
        *,
        artifact_backed: Any,
        agentic: Any,
        settings: Settings,
    ) -> None:
        self._artifact_backed = artifact_backed
        self._agentic = agentic
        self._settings = settings

    def select(self, goal_id: str | None = None) -> Any:
        strategy = resolve_effective_generation_strategy(self._settings, goal_id=goal_id)
        return self._agentic if strategy == "agentic" else self._artifact_backed


def build_generator_selector(
    settings: Settings,
    provider: ModelProvider,
    artifacts: FileArtifactStore,
    *,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    enforce_consistency: bool = True,
) -> Any:
    """Build a per-goal ``GeneratorSelector`` holding both strategy generators.

    Returns ``None`` when no model provider is configured (matches the prior
    single-generator behaviour where generation is skipped).
    """
    if provider is None:
        return None
    artifact_backed = ArtifactBackedCodeGenerator(provider, artifacts)
    agentic = AgenticCodeGenerator(
        provider,
        artifacts,
        workspace_root=Path(settings.workspace_root),
        budget=AgentBudget(
            max_turns=settings.agent_max_turns,
            max_tokens=settings.agent_max_tokens,
            max_wall_seconds=settings.agent_max_wall_seconds,
        ),
        sessions=sessions,
    )
    if enforce_consistency:
        assert_generator_consistency(strategy="artifact-backed", generator=artifact_backed)
        assert_generator_consistency(strategy="agentic", generator=agentic)
    return GeneratorSelector(
        artifact_backed=artifact_backed, agentic=agentic, settings=settings
    )
