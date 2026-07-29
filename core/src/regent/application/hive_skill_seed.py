"""Pre-installed SKILLS for the Regent hive.

Seeds common capabilities and tool specs that agents can use out-of-the-box.
These represent built-in skills that every goal's capability pool starts with,
so agents don't need to discover them externally.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import (
    CapabilityModel,
    GoalModel,
    ToolSpecModel,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """A pre-installed skill definition."""

    name: str
    capability: str
    description: str
    entrypoint: str
    constraints: dict[str, Any]
    source_url: str | None = None


# ---------------------------------------------------------------------------
# Built-in SKILLS catalog
# ---------------------------------------------------------------------------

BUILTIN_SKILLS: list[SkillDefinition] = [
    SkillDefinition(
        name="web-scraper",
        capability="web-scraping",
        description="Extract structured data from web pages via HTTP GET + HTML parsing",
        entrypoint="regent.skills.web_scraper:scrape",
        constraints={
            "allow_domains": [],
            "max_pages": 10,
            "timeout_seconds": 30,
            "require_https": True,
        },
    ),
    SkillDefinition(
        name="code-generator-python",
        capability="code-generation",
        description="Generate Python source files from structured specifications",
        entrypoint="regent.skills.code_generator:generate_python",
        constraints={
            "language": "python",
            "max_files": 50,
            "allowed_imports": ["stdlib", "pip:approved-list"],
        },
    ),
    SkillDefinition(
        name="code-generator-typescript",
        capability="code-generation",
        description="Generate TypeScript/React source files from structured specifications",
        entrypoint="regent.skills.code_generator:generate_typescript",
        constraints={
            "language": "typescript",
            "framework": "react",
            "max_files": 50,
        },
    ),
    SkillDefinition(
        name="api-client",
        capability="api-integration",
        description="Make authenticated HTTP requests to allowlisted external APIs",
        entrypoint="regent.skills.api_client:call",
        constraints={
            "allowlisted_domains": [],
            "max_retries": 3,
            "timeout_seconds": 15,
            "require_auth": True,
        },
    ),
    SkillDefinition(
        name="data-processor",
        capability="data-processing",
        description="Transform, filter, and aggregate structured data (JSON/CSV)",
        entrypoint="regent.skills.data_processor:process",
        constraints={
            "max_rows": 10000,
            "allowed_operations": ["filter", "map", "reduce", "sort", "group_by"],
        },
    ),
    SkillDefinition(
        name="test-runner",
        capability="testing",
        description="Execute test suites and collect structured results",
        entrypoint="regent.skills.test_runner:run",
        constraints={
            "frameworks": ["pytest", "jest", "vitest"],
            "timeout_seconds": 120,
            "max_parallel": 4,
        },
    ),
    SkillDefinition(
        name="file-manager",
        capability="file-operations",
        description="Read, write, and organize files within sandbox boundaries",
        entrypoint="regent.skills.file_manager:operate",
        constraints={
            "sandbox_only": True,
            "max_file_size_mb": 10,
            "allowed_extensions": [".py", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".txt"],
        },
    ),
    SkillDefinition(
        name="search-engine",
        capability="information-retrieval",
        description="Search for information using allowlisted search providers",
        entrypoint="regent.skills.search_engine:search",
        constraints={
            "providers": ["allowlisted-search-api"],
            "max_results": 10,
            "require_https": True,
        },
    ),
    SkillDefinition(
        name="dependency-installer",
        capability="dependency-management",
        description="Install and verify pip/npm dependencies within sandbox",
        entrypoint="regent.skills.dependency_installer:install",
        constraints={
            "package_managers": ["pip", "npm"],
            "sandbox_only": True,
            "require_lockfile": True,
            "approved_registries": ["pypi.org", "registry.npmjs.org"],
        },
    ),
    SkillDefinition(
        name="git-operations",
        capability="version-control",
        description="Git commit, branch, and diff operations within workspace",
        entrypoint="regent.skills.git_ops:operate",
        constraints={
            "allowed_commands": ["status", "add", "commit", "diff", "log", "branch"],
            "require_clean_working_tree": False,
        },
    ),
    SkillDefinition(
        name="schema-validator",
        capability="validation",
        description="Validate data against JSON Schema or Pydantic models",
        entrypoint="regent.skills.schema_validator:validate",
        constraints={
            "max_schema_size_kb": 100,
            "strict_mode": True,
        },
    ),
    SkillDefinition(
        name="image-processor",
        capability="media-processing",
        description="Resize, convert, and optimize images for web delivery",
        entrypoint="regent.skills.image_processor:process",
        constraints={
            "max_dimension_px": 4096,
            "allowed_formats": ["png", "jpg", "webp", "svg"],
            "sandbox_only": True,
        },
    ),
]


class HiveSkillSeedService:
    """Seed pre-installed SKILLS into a goal's capability pool."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def seed_goal_skills(self, goal_id: uuid.UUID) -> int:
        """Seed all built-in skills for a goal. Returns count of new skills seeded.

        Idempotent: skips capabilities/tools that already exist for the goal.
        """
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                logger.warning("seed skipped: goal not found", extra={"goal_id": str(goal_id)})
                return 0

            existing_caps = set(
                await session.scalars(
                    select(CapabilityModel.name).where(
                        CapabilityModel.scope_goal_id == goal_id
                    )
                )
            )
            seeded = 0
            for skill in BUILTIN_SKILLS:
                if skill.capability in existing_caps:
                    continue
                cap = CapabilityModel(
                    id=uuid.uuid4(),
                    name=skill.capability,
                    status="GOAL_CERTIFIED",
                    scope_goal_id=goal_id,
                    description=skill.description,
                    verification={
                        "skill_name": skill.name,
                        "entrypoint": skill.entrypoint,
                        "seeded": True,
                        "seed_version": "v1",
                    },
                    source_url=skill.source_url,
                    source_hash=hashlib.sha256(
                        f"{skill.name}:{skill.entrypoint}".encode()
                    ).hexdigest(),
                )
                session.add(cap)
                await session.flush()

                tool = ToolSpecModel(
                    id=uuid.uuid4(),
                    name=skill.name,
                    version=1,
                    status="CERTIFIED",
                    capability_name=skill.capability,
                    scope_goal_id=goal_id,
                    entrypoint=skill.entrypoint,
                    source_hash=hashlib.sha256(
                        f"{skill.name}:v1".encode()
                    ).hexdigest(),
                    constraints=skill.constraints,
                )
                session.add(tool)
                seeded += 1

            if seeded:
                logger.info(
                    "seeded built-in skills for goal",
                    extra={"goal_id": str(goal_id), "count": seeded},
                )
            return seeded

    def list_builtin_skills(self) -> list[dict[str, Any]]:
        """Return the catalog of built-in skills."""
        return [
            {
                "name": s.name,
                "capability": s.capability,
                "description": s.description,
                "entrypoint": s.entrypoint,
                "constraints": s.constraints,
            }
            for s in BUILTIN_SKILLS
        ]
