"""Shared frozen-plan path policy for artifact-backed and agentic generators.

Keeps fail-closed behavior for dangerous paths while accepting common
scaffold files the model tends to emit outside a narrow planned_paths set.
"""

from __future__ import annotations

from collections.abc import Iterable

DEFAULT_PLANNED_PATHS: tuple[str, ...] = (
    "src/app.py",
    "src/index.html",
    "requirements.txt",
    "README.md",
)

# Extra scaffold paths frozen into plans so generators rarely miss the set.
SCAFFOLD_PLANNED_PATHS: tuple[str, ...] = (
    "static/style.css",
    "static/app.js",
    "templates/index.html",
    "tests/__init__.py",
    "tests/test_smoke.py",
)

_ALLOWED_ROOT_FILES = frozenset(
    {
        "requirements.txt",
        "readme.md",
        "pyproject.toml",
        "index.html",
        "styles.css",
        "style.css",
        "app.js",
        "app.py",
        "main.py",
    }
)
# Keep aligned with agent/file_manifest.TEXT_EXTENSIONS (P0-2 / R0).
_ALLOWED_SUFFIXES = (
    ".html",
    ".css",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".py",
    ".md",
    ".txt",
    ".json",
    ".svg",
    ".sql",
    ".toml",
    ".yml",
    ".yaml",
)


def normalize_relative_path(relative: str) -> str:
    return relative.replace("\\", "/").strip()


def is_allowed_extra_path(relative: str) -> bool:
    """Return True for safe scaffold paths outside a narrow planned set."""
    name = normalize_relative_path(relative).lower()
    if not name or name.startswith("../") or "/../" in name or name.startswith(".regent"):
        return False
    if name in _ALLOWED_ROOT_FILES:
        return True
    # Require both an allowlisted prefix and a safe suffix (no arbitrary paths).
    if not name.endswith(_ALLOWED_SUFFIXES):
        return False
    return (
        name.startswith("tests/")
        or name.startswith("static/")
        or name.startswith("templates/")
        or name.startswith("src/")
    )


def is_path_within_frozen_plan(relative: str, planned_paths: Iterable[str]) -> bool:
    """Accept planned paths or allowlisted scaffold extras."""
    normalized = normalize_relative_path(relative)
    planned = {normalize_relative_path(p) for p in planned_paths}
    if normalized in planned:
        return True
    return is_allowed_extra_path(normalized)


def expand_planned_paths(
    planned_paths: Iterable[str] | None,
    *,
    goal_scale: str | None = None,
) -> list[str]:
    """Ensure mandatory + scaffold paths are present on a frozen plan."""
    paths: list[str] = []
    seen: set[str] = set()
    seed = list(planned_paths) if planned_paths else list(DEFAULT_PLANNED_PATHS)
    for item in [*seed, *SCAFFOLD_PLANNED_PATHS]:
        normalized = normalize_relative_path(str(item))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        paths.append(normalized)
    for mandatory in ("requirements.txt", "README.md"):
        if mandatory not in seen:
            paths.append(mandatory)
            seen.add(mandatory)
    scale = (goal_scale or "").upper()
    if scale != "SMALL" and not any(
        p.lower().startswith("tests/") or p.lower().startswith("test_") for p in paths
    ):
        paths.append("tests/test_smoke.py")
    return paths
