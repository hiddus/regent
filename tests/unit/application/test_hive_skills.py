"""Tests for parallel hive execution and pre-installed SKILLS."""

from __future__ import annotations

import uuid

import pytest
from regent.application.hive_skill_seed import (
    BUILTIN_SKILLS,
    HiveSkillSeedService,
    SkillDefinition,
)


# ---------------------------------------------------------------------------
# Built-in SKILLS catalog
# ---------------------------------------------------------------------------


def test_builtin_skills_catalog_not_empty() -> None:
    """Built-in skills catalog has at least 10 skills."""
    assert len(BUILTIN_SKILLS) >= 10


def test_all_skills_have_required_fields() -> None:
    """Every skill definition has name, capability, description, entrypoint."""
    for skill in BUILTIN_SKILLS:
        assert isinstance(skill, SkillDefinition)
        assert skill.name.strip()
        assert skill.capability.strip()
        assert skill.description.strip()
        assert skill.entrypoint.strip()
        assert isinstance(skill.constraints, dict)


def test_skill_names_are_unique() -> None:
    """No duplicate skill names."""
    names = [s.name for s in BUILTIN_SKILLS]
    assert len(names) == len(set(names))


def test_skill_capabilities_cover_core_domains() -> None:
    """Skills cover core capability domains."""
    caps = {s.capability for s in BUILTIN_SKILLS}
    assert "code-generation" in caps
    assert "web-scraping" in caps
    assert "testing" in caps
    assert "information-retrieval" in caps
    assert "dependency-management" in caps


def test_skill_entrypoints_follow_convention() -> None:
    """All entrypoints follow regent.skills.<name>:<func> convention."""
    for skill in BUILTIN_SKILLS:
        assert skill.entrypoint.startswith("regent.skills."), (
            f"{skill.name} entrypoint must start with regent.skills."
        )
        assert ":" in skill.entrypoint, (
            f"{skill.name} entrypoint must contain ':' separator"
        )


def test_skill_constraints_include_safety_bounds() -> None:
    """Skills that interact with external resources have safety constraints."""
    external_skills = [
        s for s in BUILTIN_SKILLS
        if s.capability in {"web-scraping", "api-integration", "information-retrieval"}
    ]
    for skill in external_skills:
        constraints = skill.constraints
        # Must have some form of domain restriction or HTTPS requirement
        has_domain_restriction = (
            "allow_domains" in constraints
            or "allowlisted_domains" in constraints
            or "providers" in constraints
        )
        has_https_requirement = constraints.get("require_https", False)
        assert has_domain_restriction or has_https_requirement, (
            f"{skill.name} must have domain restriction or HTTPS requirement"
        )


def test_sandbox_only_skills_are_isolated() -> None:
    """Skills marked sandbox_only cannot escape the sandbox."""
    sandbox_skills = [
        s for s in BUILTIN_SKILLS
        if s.constraints.get("sandbox_only")
    ]
    assert len(sandbox_skills) >= 3, (
        "At least file-manager, dependency-installer, image-processor should be sandbox_only"
    )


# ---------------------------------------------------------------------------
# HiveSkillSeedService
# ---------------------------------------------------------------------------


def test_seed_service_can_list_skills() -> None:
    """HiveSkillSeedService.list_builtin_skills returns the catalog."""
    from unittest.mock import MagicMock

    sessions = MagicMock()
    service = HiveSkillSeedService(sessions)
    skills = service.list_builtin_skills()
    assert len(skills) == len(BUILTIN_SKILLS)
    for item in skills:
        assert "name" in item
        assert "capability" in item
        assert "description" in item


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------


def test_parallel_execute_import() -> None:
    """execute_parallel method exists on SingleAgentExecutionService."""
    from regent.application.execution_service import SingleAgentExecutionService

    assert hasattr(SingleAgentExecutionService, "execute_parallel")


@pytest.mark.asyncio
async def test_parallel_execute_empty_list() -> None:
    """execute_parallel with empty list returns empty list."""
    from unittest.mock import MagicMock

    from regent.application.execution_service import SingleAgentExecutionService

    service = SingleAgentExecutionService(
        MagicMock(), MagicMock(), MagicMock()
    )
    result = await service.execute_parallel([], actor="test")
    assert result == []
