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
